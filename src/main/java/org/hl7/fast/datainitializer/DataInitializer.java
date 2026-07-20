package org.hl7.fast.datainitializer;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

import org.hl7.fhir.instance.model.api.IBaseResource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Conditional;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.Resource;
import org.springframework.core.io.ResourceLoader;
import org.springframework.core.io.support.ResourcePatternResolver;
import org.springframework.core.io.support.ResourcePatternUtils;
import org.springframework.util.FileCopyUtils;

import ca.uhn.fhir.context.FhirContext;
import ca.uhn.fhir.jpa.api.dao.DaoRegistry;
import ca.uhn.fhir.jpa.api.dao.IFhirResourceDao;
import ca.uhn.fhir.rest.api.server.SystemRequestDetails;
import jakarta.annotation.PostConstruct;

@Configuration
@Conditional(NonEmptyInitialDataCondition.class)
public class DataInitializer {

  private static final Logger logger = LoggerFactory.getLogger(DataInitializer.class);

  // Resources are loaded in parallel a chunk at a time; the load is bound by
  // per-resource CPU (parsing and search parameter extraction), not by the
  // database. Chunking keeps memory flat when streaming large NDJSON files.
  private static final int CHUNK_SIZE = 2000;

  @Autowired
  private FhirContext fhirContext;

  @Autowired
  private DaoRegistry daoRegistry;

  @Autowired
  private DataInitializerProperties dataInitializerProperties;

  @Autowired
  private ResourceLoader resourceLoader;

  record LoadItem(String label, String text) {}

  @FunctionalInterface
  interface ItemConsumer {
    void accept(LoadItem item) throws IOException;
  }

  // Runs during context initialization, so the FHIR endpoint does not accept
  // requests until the seed data is fully loaded.
  @PostConstruct
  public void initializeData() {

    if (dataInitializerProperties.getInitialData() == null || dataInitializerProperties.getInitialData().isEmpty()) {
      return;
    }

    logger.info("Initializing data");
    long startNanos = System.nanoTime();
    long totalLoaded = 0;
    long totalFailed = 0;
    int directories = 0;

    for (String directoryPath : dataInitializerProperties.getInitialData()) {
      logger.info("Loading resources from directory: " + directoryPath);

      try {
        List<LoadItem> failed = Collections.synchronizedList(new ArrayList<>());
        long loadedTotal = loadDirectory(directoryPath, failed);
        int pass = 1;
        logger.info("Pass {} complete. Loaded {} resources ({} remaining).", pass, loadedTotal, failed.size());

        // Retry loop to allow out-of-order loading while keeping referential
        // integrity enabled. Resources whose references were missing load on a
        // later pass; stops when a pass makes no progress.
        while (!failed.isEmpty()) {
          pass++;
          List<LoadItem> retry = new ArrayList<>();
          long loadedThisPass = 0;
          for (LoadItem item : failed) {
            if (load(item, retry)) {
              loadedThisPass++;
            }
          }
          failed = retry;
          loadedTotal += loadedThisPass;
          logger.info("Pass {} complete. Loaded {} resources ({} remaining).", pass, loadedThisPass, failed.size());
          if (loadedThisPass == 0) {
            break;
          }
        }
        for (LoadItem item : failed) {
          logger.warn("Failed to load resource after {} passes: {}", pass, item.label());
        }
        logger.info("Finished loading directory {}. Loaded {} resources.", directoryPath, loadedTotal);
        totalLoaded += loadedTotal;
        totalFailed += failed.size();
        directories++;
      } catch (Exception e) {
        logger.error("Error loading resources from directory: " + directoryPath, e);
      }
    }

    logger.info("Data initialization complete in {}s: loaded {} resources ({} failed) across {} directories.",
        String.format("%.1f", (System.nanoTime() - startNanos) / 1_000_000_000.0),
        totalLoaded, totalFailed, directories);
  }

  private long loadDirectory(String directoryPath, List<LoadItem> failed) throws IOException {
    ResourcePatternResolver resolver = ResourcePatternUtils.getResourcePatternResolver(resourceLoader);
    long[] processed = {0};
    List<LoadItem> chunk = new ArrayList<>();

    for (Resource resource : sortedResources(resolver, "classpath:" + directoryPath + "/**/*.json")) {
      try {
        String text = new String(FileCopyUtils.copyToByteArray(resource.getInputStream()), StandardCharsets.UTF_8);
        chunk.add(new LoadItem(resource.getFilename(), text));
        if (chunk.size() >= CHUNK_SIZE) {
          processed[0] += loadChunk(chunk, failed);
        }
      } catch (Exception e) {
        logger.error("Failed to read {}: {}", resource.getFilename(), e.getMessage());
      }
    }

    for (Resource resource : sortedResources(resolver, "classpath:" + directoryPath + "/**/*.ndjson")) {
      try {
        forEachNdjsonItem(resource, item -> {
          chunk.add(item);
          if (chunk.size() >= CHUNK_SIZE) {
            processed[0] += loadChunk(chunk, failed);
          }
        });
      } catch (Exception e) {
        logger.error("Failed to read {}: {}", resource.getFilename(), e.getMessage());
      }
    }

    processed[0] += loadChunk(chunk, failed);
    return processed[0] - failed.size();
  }

  // Items in a chunk load concurrently; two items racing to auto-create the
  // same placeholder reference target can fail on a unique index, which the
  // retry pass then loads cleanly.
  private long loadChunk(List<LoadItem> chunk, List<LoadItem> failed) {
    if (chunk.isEmpty()) {
      return 0;
    }
    List<LoadItem> items = new ArrayList<>(chunk);
    chunk.clear();
    items.parallelStream().forEach(item -> load(item, failed));
    return items.size();
  }

  // Deterministic path order lets a data set encode its load order in file
  // names (e.g. 01-Organization.ndjson loads before 07-PractitionerRole.ndjson),
  // which the resolver's platform-dependent enumeration would not guarantee.
  private static Resource[] sortedResources(ResourcePatternResolver resolver, String pattern) throws IOException {
    Resource[] resources = resolver.getResources(pattern);
    Arrays.sort(resources, Comparator.comparing(DataInitializer::resourcePath));
    return resources;
  }

  private static String resourcePath(Resource resource) {
    try {
      return resource.getURI().toString();
    } catch (IOException e) {
      return String.valueOf(resource.getFilename());
    }
  }

  static void forEachNdjsonItem(Resource resource, ItemConsumer consumer) throws IOException {
    try (BufferedReader reader = new BufferedReader(
        new InputStreamReader(resource.getInputStream(), StandardCharsets.UTF_8))) {
      String line;
      int lineNumber = 0;
      while ((line = reader.readLine()) != null) {
        lineNumber++;
        line = line.trim();
        if (!line.isEmpty()) {
          consumer.accept(new LoadItem(resource.getFilename() + ":" + lineNumber, line));
        }
      }
    }
  }

  private boolean load(LoadItem item, List<LoadItem> failed) {
    try {
      IBaseResource fhirResource = fhirContext.newJsonParser().parseResource(item.text());
      IFhirResourceDao<IBaseResource> dao = daoRegistry.getResourceDao(fhirResource);
      dao.update(fhirResource, new SystemRequestDetails());
      return true;
    } catch (Exception e) {
      logger.trace("Deferring resource {} until dependencies exist: {}", item.label(), e.getMessage());
      failed.add(item);
      return false;
    }
  }
}
