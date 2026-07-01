# Authentication

POST /auth/login

POST /auth/register

GET /auth/me

---

# Dashboard

GET /dashboard/overview

GET /dashboard/queue

GET /dashboard/recent-activity

---

# Work Items

POST /work-items/upload

GET /work-items

GET /work-items/{id}

DELETE /work-items/{id}

POST /work-items/{id}/reprocess

...

For every endpoint include:

- Purpose
- Authentication required?
- Request body
- Response body
- Status codes