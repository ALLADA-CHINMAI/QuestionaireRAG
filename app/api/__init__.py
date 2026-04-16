# app/api — FastAPI route definitions
#
# Modules in this package:
#   routes — all HTTP endpoints:
#              GET  /health                  — liveness + index status check
#              POST /index/questionnaires    — parse PSmart questions and build indexes
#              POST /query                   — retrieve ranked questions for a customer
