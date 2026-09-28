"""ARCH48-S1:collab — Real-Time Collaborative Review & Live Presence.

  vocabulary  the protocol, limits and names (pure; no imports of the app)
  service     THE clock (`now()`), soft locks, resolution versions
  anchors     a document's paragraphs, their digests and anchor re-finding (pure)
  threads     discussion threads and comments
  events      live events, published only after the transaction commits
  broker      fan-out across API workers (Redis pub/sub) and shared presence
  hub         the per-process WebSocket connection manager
  gate        the capability check shared by REST, the WebSocket and the hub
"""
