package org.hl7.fast.datainitializer;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
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

  @Autowired
  private FhirContext fhirContext;

  @Autowired
  private DaoRegistry daoRegistry;

  @Autowired
  private DataInitializerProperties dataInitializerProperties;

  @Autowired
  private ResourceLoader resourceLoader;

  // One resource to load: a label for logging plus its raw JSON text. A .json
  // file contributes one item; a .ndjson file contributes one item per line.
  private record LoadItem(String label, String text) {}

  @PostConstruct
  public void initializeData() {

    if (dataInitializerProperties.getInitialData() == null || dataInitializerProperties.getInitialData().isEmpty()) {
      return;
    }

    logger.info("Initializing data");

    for (String directoryPath : dataInitializerProperties.getInitialData()) {
      logger.info("Loading resources from directory: " + directoryPath);

      List<LoadItem> queue;
      try {
        queue = collectItems(directoryPath);
      } catch (Exception e) {
        logger.error("Error loading resources from directory: " + directoryPath, e);
        continue;
      }

      // Retry loop to allow out-of-order loading while keeping referential integrity enabled.
      // If a resource fails due to missing references, we defer it to a later pass.
      int pass = 0;
      int loadedTotal = 0;

      while (!queue.isEmpty()) {
        pass++;
        int loadedThisPass = 0;

        Iterator<LoadItem> it = queue.iterator();
        while (it.hasNext()) {
          LoadItem item = it.next();
          try {
            IBaseResource fhirResource = fhirContext.newJsonParser().parseResource(item.text());
            IFhirResourceDao<IBaseResource> dao = daoRegistry.getResourceDao(fhirResource);
            dao.update(fhirResource, new SystemRequestDetails());
            it.remove();
            loadedThisPass++;
            loadedTotal++;
            logger.debug("Loaded resource: {} (pass {})", item.label(), pass);
          } catch (Exception e) {
            // Defer and try again in the next pass
            logger.trace("Deferring resource {} until dependencies exist: {}", item.label(), e.getMessage());
          }
        }

        logger.info("Pass {} complete. Loaded {} resources ({} remaining).", pass, loadedThisPass, queue.size());

        if (loadedThisPass == 0) {
          // No progress made; break to avoid infinite loop and report failures.
          for (LoadItem remaining : queue) {
            logger.warn("Failed to load resource after {} passes: {}", pass, remaining.label());
          }
          break;
        }
      }
      logger.info("Finished loading directory {}. Loaded {} resources.", directoryPath, loadedTotal);

    }

  }

  // Collect load items from both *.json files (one resource each) and *.ndjson
  // files (one resource per line) anywhere under the directory.
  private List<LoadItem> collectItems(String directoryPath) throws Exception {
    ResourcePatternResolver resolver = ResourcePatternUtils.getResourcePatternResolver(resourceLoader);
    List<LoadItem> items = new ArrayList<>();

    for (Resource resource : resolver.getResources("classpath:" + directoryPath + "/**/*.json")) {
      try {
        String text = new String(FileCopyUtils.copyToByteArray(resource.getInputStream()), StandardCharsets.UTF_8);
        items.add(new LoadItem(resource.getFilename(), text));
      } catch (Exception e) {
        logger.error("Failed to read {}: {}", resource.getFilename(), e.getMessage());
      }
    }

    for (Resource resource : resolver.getResources("classpath:" + directoryPath + "/**/*.ndjson")) {
      try {
        String content = new String(FileCopyUtils.copyToByteArray(resource.getInputStream()), StandardCharsets.UTF_8);
        String[] lines = content.split("\\r?\\n");
        for (int i = 0; i < lines.length; i++) {
          String line = lines[i].trim();
          if (!line.isEmpty()) {
            items.add(new LoadItem(resource.getFilename() + ":" + (i + 1), line));
          }
        }
      } catch (Exception e) {
        logger.error("Failed to read {}: {}", resource.getFilename(), e.getMessage());
      }
    }

    return items;
  }
}
