---
action: backend.answer_question
expect:
  terminal: draft
  agents: [backend.explain]
  toolsets:
    backend.explain: [backend.docs]
---
tài liệu deploy của orders-api nói gì về cách rollback vậy a?
