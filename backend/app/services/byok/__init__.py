"""ARCH-22 — bring-your-own-key credentials, client construction and routing.

Split three ways on purpose:

  credential_service   persistence, encryption, rotation, live validation
  provider_clients     ProviderClientFactory — unshared per-call clients
  model_routing_service resolution of (task -> provider, model, credential)

`provider_clients` must not import `model_routing_service`, and neither may
import the API layer. The dependency runs one way: routing decides, the
factory builds, the credential service supplies the plaintext for exactly as
long as the call takes.
"""
