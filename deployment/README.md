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

# What answered, and what it is allowed to do. One InvokeAgentRuntime call,
# no model spend: the runtime's `status` operation.
python -m countersign.cli runtime
```

In that mode the console builds no local model. `run_onboarding` and
`run_review` call `InvokeAgentRuntime` through
`countersign/agentcore_client.py`, and the console re-runs the deterministic
test on its own side, so every review carries two independent counts of the
population. They should agree; the run's trace says `runtime.count.agreed` when
they do and `runtime.count.disagreed` when they do not, and the console's count
is the published one either way.

A runtime that cannot be reached degrades a review to the deterministic composer
and records it. Onboarding, which has nothing honest to fall back to, fails.

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

## Connecting a real source

Set `GITHUB_ORG` and `GITHUB_TOKEN` (read scopes) and the change-approval
control runs against a real organisation with no other change. Note what happens
to the rest of the estate when you do: a source with no credentials becomes
*disconnected* rather than falling back to the seeded corpus, so a control that
needs it records untested population and reports `inconclusive`. That is
deliberate. A second-line product must not publish one outcome over real GitHub
merges and a fictional leaver list. `COUNTERSIGN_ALLOW_SOURCE_MIXING=true` lifts
it for a demonstration and should stay off anywhere else.
