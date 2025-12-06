package org.hl7.fast;

import java.util.ArrayList;

import org.hl7.fast.datainitializer.DataInitializerProperties;
import org.springframework.beans.BeansException;
import org.springframework.beans.factory.config.BeanPostProcessor;
import org.springframework.context.annotation.Configuration;

import ca.uhn.fhir.jpa.starter.AppProperties;

/**
 * Test configuration to reset custom defaults that are otherwise set in the common application.yaml.
 * Changes placed here won't have to be merged with upstream changes to HAPI starter files.
 */
@Configuration
public class CustomTestConfig implements BeanPostProcessor {

  @Override
  public Object postProcessAfterInitialization(Object bean, String beanName) throws BeansException {
    
    if (bean instanceof AppProperties appProperties) {
      appProperties.setImplementationGuides(null);
      appProperties.setSupported_resource_types(new ArrayList<>());
    }

    if (bean instanceof DataInitializerProperties dataInitializerProperties) {
      dataInitializerProperties.setInitialData(null);
    }

    return bean;
  }

}
