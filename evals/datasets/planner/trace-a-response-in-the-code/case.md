---
action: backend.trace_problem
expect:
  terminal: draft
  agents: [backend.diagnose]
  toolsets:
    backend.diagnose: [backend.code]
---
a ơi item ai_edit không có thumbnail, đoạn nào trả về cái này vậy a {"statusCode":200,"data":[{"key":"ai_edit","thumbnail":null}]}
