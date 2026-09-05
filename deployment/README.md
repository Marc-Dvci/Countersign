# Deployment

Two images, two jobs.

`Dockerfile` builds the console: the API, the deterministic control tests, the
SQLite store and the static UI. It is the sole writer of the database and wants
`/app/data` on durable storage.

`Dockerfile.agentcore` builds the Bedrock AgentCore runtime: the Strands agents
and nothing else. It is stateless, and the role in
`runtime-permissions-policy.json` grants model invocation and telemetry only. It
has no database credentials, so a compromised runtime cannot reach a tenant's
canonical state, propose itself an approval, or close a finding.

## The runtime

```bash
aws ecr create-repository --repository-name countersign --region eu-west-1
aws ecr get-login-password --region eu-west-1 \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.eu-west-1.amazonaws.com

# AgentCore runs ARM64.
docker buildx build --platform linux/arm64 -f Dockerfile.agentcore \
  -t <account>.dkr.ecr.eu-west-1.amazonaws.com/countersign:latest --push .

aws iam create-role --role-name CountersignAgentCoreRuntime \
  --assume-role-policy-document file://deployment/runtime-trust-policy.json
aws iam put-role-policy --role-name CountersignAgentCoreRuntime \
  --policy-name countersign-runtime \
  --policy-document file://deployment/runtime-permissions-policy.json

python deployment/deploy_agentcore.py \
  --image <account>.dkr.ecr.eu-west-1.amazonaws.com/countersign:latest \
  --role  arn:aws:iam::<account>:role/CountersignAgentCoreRuntime
```

Then point the console at it:

```bash
export COUNTERSIGN_MODEL_MODE=agentcore
export COUNTERSIGN_AGENTCORE_ARN=<the ARN the deploy script printed>
```

## The console

```bash
docker build -t countersign .
docker run -p 8080:8080 -v countersign-data:/app/data \
  -e COUNTERSIGN_CONSOLE_PASSWORD=<something of your own> countersign
```

Before it is reachable from anywhere but localhost, change
`COUNTERSIGN_CONSOLE_PASSWORD`, put TLS in front of it, and set
`COUNTERSIGN_AS_OF` to today if the connectors are live. The default `as_of` is
the date the seeded estate is anchored to, which keeps the demonstration stable
and would be wrong for a real tenant.

## Model access

Bedrock model access is granted per account, per model, per region. A valid key
is not enough: the model has to be enabled in the console for the region you are
calling, and a brand-new account may sit in verification before any of it works.
`COUNTERSIGN_MODEL_MODE=demo` runs the whole product deterministically in the
meantime and is what the seeded demonstration uses.
