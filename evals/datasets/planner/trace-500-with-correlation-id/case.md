---
action: backend.trace_problem
memory:
  - "orders-api production logs are in Loki; dev logs only through kubectl"
expect:
  terminal: draft
  agents: [backend.diagnose]
---
a ơi POST https://api.example.com/v1/orders trả 500 từ sáng, correlationId 3f2b9c1e-8a4d-4e6f-9b1a-2c7d5e8f0a13, a check giúp e với
