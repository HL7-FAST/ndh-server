package org.hl7.fast.common;

import org.springframework.beans.factory.annotation.Autowired;

import ca.uhn.fhir.rest.server.RestfulServer;
import jakarta.annotation.PostConstruct;

public abstract class BaseInterceptor {

  @Autowired
  protected RestfulServer restfulServer;

  @PostConstruct
  protected void registerInterceptor() {
    restfulServer.registerInterceptor(this);
  }
  
}
