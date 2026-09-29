---
name: trace-a-purchase
description: Follow one in-app purchase from the store webhook to the coin grant.
---
1. Find the store webhook for the user in payments-api logs.
2. Follow the grant event to wallet-api; a missing grant line is the cause.
