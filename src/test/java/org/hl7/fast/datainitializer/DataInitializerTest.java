package org.hl7.fast.datainitializer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.Test;
import org.springframework.core.io.InputStreamResource;
import org.springframework.core.io.Resource;

class DataInitializerTest {

  @Test
  void streamsNdjsonInsteadOfBufferingTheWholeFile() throws Exception {
    String line = "{\"resourceType\":\"Location\",\"id\":\"one\"}\n";
    byte[] data = line.repeat(2_000).getBytes(StandardCharsets.UTF_8);
    ByteArrayInputStream input = new ByteArrayInputStream(data);
    Resource resource = new InputStreamResource(input) {
      @Override
      public String getFilename() {
        return "Location.ndjson";
      }
    };
    AtomicInteger seen = new AtomicInteger();

    DataInitializer.forEachNdjsonItem(resource, item -> {
      if (seen.incrementAndGet() == 1) {
        assertTrue(input.available() > 0);
        assertEquals("Location.ndjson:1", item.label());
      }
    });

    assertEquals(2_000, seen.get());
  }
}
