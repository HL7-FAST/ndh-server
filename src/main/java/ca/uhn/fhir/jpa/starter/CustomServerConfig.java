package ca.uhn.fhir.jpa.starter;

import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;

@Configuration
@ComponentScan(basePackages = { "org.hl7.fast" })
public class CustomServerConfig {
  
}
