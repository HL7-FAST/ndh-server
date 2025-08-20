# FAST National Directory Reference Implementation

This is a FHIR server reference implementation of the [FAST National Directory of Healthcare Providers & Services (NDH) IG](https://build.fhir.org/ig/HL7/fhir-us-ndh/) for the current STU2 sequence.  It is built on the [HAPI FHIR JPA Starter Project](https://github.com/hapifhir/hapi-fhir-jpaserver-starter) project and more detailed configuration information can be found in that repository.

## Prerequisites
Building and running the server locally requires either Docker or
- Java 17+
- Maven

## Building and Running the Server

There are multiple ways to build and run the server locally.  By default, the server's base FHIR endpoint will be available at http://localhost:8080/fhir

### Using Maven


```bash
mvn spring-boot:run
```
or
```bash
mvn -Pjetty spring-boot:run
```

### Using Docker

```bash
docker compose up -d
```
