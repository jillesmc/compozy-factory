# Contract: `deploy` (deploy-finished signal)

The deployment of a merge commit (deployment = code merge SHA, BR-17). Read only: the factory never
queries the deploy platform itself.

| Operation | Input | Output | Errors |
| --- | --- | --- | --- |
| deploy.wait | merge sha, environment, bound (minutes) | {outcome success/failure/error/absent, finished_at, link} | deploy_unavailable |
