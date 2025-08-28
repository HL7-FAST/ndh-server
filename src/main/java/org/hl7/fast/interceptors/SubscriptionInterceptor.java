package org.hl7.fast.interceptors;

import java.util.List;
import java.util.stream.Collectors;

import org.hl7.fast.common.BaseInterceptor;
import org.hl7.fhir.instance.model.api.IBaseResource;
import org.hl7.fhir.r4.model.Bundle;
import org.hl7.fhir.r4.model.Parameters;
import org.hl7.fhir.r4.model.Practitioner;
import org.hl7.fhir.r4.model.StringType;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Import;
import org.springframework.stereotype.Component;

import ca.uhn.fhir.interceptor.api.Hook;
import ca.uhn.fhir.interceptor.api.Pointcut;
import ca.uhn.fhir.jpa.api.dao.DaoRegistry;
import ca.uhn.fhir.jpa.patch.FhirPatch;
import ca.uhn.fhir.jpa.topic.SubscriptionTopicConfig;
import ca.uhn.fhir.jpa.topic.SubscriptionTopicDispatcher;
import ca.uhn.fhir.rest.api.RestOperationTypeEnum;
import ca.uhn.fhir.rest.api.server.RequestDetails;

@Component
@Import(SubscriptionTopicConfig.class)
public class SubscriptionInterceptor extends BaseInterceptor {
  

  private final static Logger logger = LoggerFactory.getLogger(SubscriptionInterceptor.class);

  @Autowired
  DaoRegistry daoRegistry;

  @Autowired
  SubscriptionTopicDispatcher subscriptionTopicDispatcher;


  @Hook(Pointcut.STORAGE_PRECOMMIT_RESOURCE_CREATED)
  public void created(IBaseResource theResource, RequestDetails theRequestDetails) {
    process(theResource, null, RestOperationTypeEnum.CREATE, theRequestDetails);
  }

  @Hook(Pointcut.STORAGE_PRECOMMIT_RESOURCE_UPDATED)
  public void updated(IBaseResource theOldResource, IBaseResource theResource, RequestDetails theRequestDetails) {
    process(theResource, theOldResource, RestOperationTypeEnum.UPDATE, theRequestDetails);
  }

  @Hook(Pointcut.STORAGE_PRECOMMIT_RESOURCE_DELETED)
  public void deleted(IBaseResource theResource, RequestDetails theRequestDetails) {
    process(theResource, null, RestOperationTypeEnum.DELETE, theRequestDetails);
  }


  protected void process(IBaseResource resource, IBaseResource oldResource, RestOperationTypeEnum operationType, RequestDetails theRequestDetails) {

    logger.info("Processing {} operation for resource of type {}", operationType.name(), resource.fhirType());
    
    // currently only Practitioner has a topic in NDH STU2
    String type = resource.fhirType();
    if (!type.equals("Practitioner")) {
      return;
    }

    // check for a change in the qualification list if an old resource is present
    if (oldResource != null) {
      if (pathHasChanged(oldResource, resource, "Practitioner.qualification", theRequestDetails)) {
        dispatch("http://ndh.org/topic/practitioner-qualification-create-modified-or-delete", resource, operationType);
      }
    }
    // otherwise if this is a create operation, check if the new resource has qualifications
    else if (operationType == RestOperationTypeEnum.CREATE && ((Practitioner)resource).hasQualification()) {
      dispatch("http://ndh.org/topic/practitioner-qualification-create-modified-or-delete", resource, operationType);
    }

    
  }

  protected void dispatch(String topic, IBaseResource resource, RestOperationTypeEnum operationType) {
    subscriptionTopicDispatcher.dispatch(topic, List.of(resource), operationType);
  }

  protected void dispatch(String topic, Bundle bundle, RestOperationTypeEnum operationType) {
    subscriptionTopicDispatcher.dispatch(topic, 
      bundle.getEntry().stream().map(e -> e.getResource()).collect(Collectors.toList()), 
      operationType);
  }


  protected Boolean pathHasChanged(IBaseResource oldResource, IBaseResource newResource, String path, RequestDetails theRequestDetails) {
    
    FhirPatch patch = new FhirPatch(theRequestDetails.getFhirContext());
    var diff = patch.diff(oldResource, newResource);
    
    Boolean hasChanged = false;
    for (var param : ((Parameters) diff).getParameter()) {
      if (param.getName().equals("operation")) {
        hasChanged = param.getPart().stream().anyMatch(part -> 
            part.getName().equals("path")
            && part.getValue() instanceof StringType
            && ((StringType) part.getValue()).getValue().startsWith(path));
      }
      if (hasChanged) {
        break;
      }
    }

    return hasChanged;

  }

}
