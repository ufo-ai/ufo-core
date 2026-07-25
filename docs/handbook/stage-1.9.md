# Sandbox deployment safety gates  `stage-1.9`

This stage is a set of deployment “preflight” checks. It runs before real traffic is allowed, like testing the brakes and lights before a vehicle leaves the garage. Its job is to prove that sandbox environments can use the same key infrastructure they will depend on in production-like use.

The mount gate checks workspace storage. It starts a real sandbox, connects the workspace storage the same way production does, writes a small test file, and reads it back. If the sandbox cannot mount the storage or the file does not survive the round trip, the deployment is stopped.

The proxy gate checks secure web access through the sandbox proxy. It creates a fresh sandbox outside the main cluster, installs the required trusted certificate, then uses HTTPS, the encrypted web protocol, to reach the proxy and confirm the expected response. Together, these gates catch broken storage or certificate routing before users can hit them.

## Files in this stage

### Deployment Safety Gates
Preflight checks verify that sandbox workspace mounting and HTTPS proxy certificate routing work before traffic is served.

### `sandbox/mount_gate.py`

`entrypoint` · `deployment validation`

This script answers one practical question: “Can a freshly deployed sandbox really use its /workspace folder?” That folder is backed by cloud storage, so several things must all work together: the sandbox template, temporary AWS credentials, the S3 bucket, the mount command, and the health check used by running agents. If any of those are wrong, agents may later get stuck because their workspace cannot be used. This file catches that early.

When run from the command line, it takes an AWS role, bucket, and region. It creates a throwaway conversation id, which acts like a unique folder name in the bucket. It then asks the same credential-minter used by production to create short-lived, prefix-scoped credentials. “Prefix-scoped” means the credentials only allow access to that one temporary workspace area, not the whole bucket.

Next it creates an E2B sandbox from the published template. Inside that sandbox it creates the workspace directory, writes the AWS credentials file, runs the normal preparation and mount scripts, and runs the standard mount health check. Finally it behaves like a small agent: it creates a file, lists it, reads it back, changes its permissions, and deletes it. The sandbox is killed in a finally block, so cleanup happens even if the test fails. A failure exits with an error, making the deployment pipeline go red instead of letting a broken storage setup reach users.

#### Function details

##### `main`  (lines 46–78)

```
def main() -> None
```

**Purpose**: This is the command-line entry point for the mount gate. It proves that a real sandbox can mount and use its workspace before a deployment is considered healthy.

**Data flow**: It starts with command-line inputs: the AWS role ARN, S3 bucket name, and AWS region. From those it builds the S3 endpoint, creates a unique temporary workspace id, mints short-lived AWS credentials, builds the mount commands, and starts a sandbox. It writes the credentials into the sandbox, runs the prepare and mount steps, checks mount health, runs a simple file exercise, prints the exercise output, kills the sandbox, and finally prints a success message. Its main outside effect is creating and then destroying a temporary sandbox, while briefly touching a throwaway workspace prefix in the bucket.

**Call relations**: This function is the whole flow for this file. It uses argparse to read deployment parameters, calls the credential-minter pieces to get safe temporary storage access, asks the mount helper functions to produce the same scripts production carriers use, and creates an E2B Sandbox to run those scripts for real. It hands the generated credentials and commands into the sandbox, then relies on the mount health check and the file exercise to prove the storage path works end to end.

*Call graph*: 11 external calls (__init__, __init__, ArgumentParser, run, create, workspace_key_prefix, aws_credentials_file, mount_health_check, mount_scripts, s3fs_command (+1 more)).


### `sandbox/proxy_gate.py`

`entrypoint` · `deploy validation`

This script acts like a deploy-time gate: it does not provide the proxy itself, but it checks that the proxy path is usable from a real sandbox. That matters because the rest of the system may depend on sandboxed code sending outbound HTTPS requests through this proxy. If the proxy URL is wrong, the certificate is not trusted, or the route is not ready yet, later sandbox work would fail in harder-to-understand ways.

The script takes a proxy URL from the command line and reads a certificate from an environment variable. It creates a temporary E2B sandbox, which is an isolated remote runtime, similar to spinning up a clean test machine. Inside that sandbox it writes the certificate file and runs the project’s certificate installation command so HTTPS tools trust the proxy’s TLS certificate.

Then it repeatedly runs a small curl test. curl is a command-line web request tool. The test asks curl to connect through the proxy to Anthropic’s API using a deliberately invalid token. A successful proxy connection is expected to return HTTP CONNECT status 403, meaning the proxy was reached and rejected the fake credentials as intended. Some connection errors are treated as “not ready yet,” so the script waits and retries for a limited time. Whether it succeeds or fails, it kills the temporary sandbox at the end so no test machine is left running.

#### Function details

##### `ProxyTlsGate.run`  (lines 45–109)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy readiness check inside a temporary sandbox. It verifies that the proxy URL is HTTPS, installs the provided certificate in the sandbox, then probes the proxy until it either sees the expected rejection response or times out.

**Data flow**: It starts with the gate’s stored proxy URL and certificate text. It parses the URL, builds a curl command that uses the proxy with a fake run token, creates a sandbox, writes and installs the certificate there, and runs the probe command repeatedly. If curl reports the expected CONNECT status, it prints a success message and returns. If the response is malformed, unexpected, or never becomes ready in time, it raises an error. In all cases, it shuts down the sandbox before leaving.

**Call relations**: This is called by main after command-line and environment inputs have been collected. During the check it relies on external tools: it creates an E2B sandbox, uses shell quoting to build a safe curl command, checks the clock to enforce the retry deadline, and sleeps between retries while the proxy may still be coming up.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 112–119)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the proxy gate. It collects the required proxy URL argument and certificate environment variable, then starts the readiness check.

**Data flow**: It reads '--proxy-url' from the command line and reads the certificate text from the configured environment variable. If the certificate is missing, it raises an error immediately. Otherwise it creates a ProxyTlsGate with those two pieces of information and calls its run method; the result is either a successful check or an exception explaining why the gate failed.

**Call relations**: This is the top-level function used when the file is run as a script. It prepares the inputs and hands off to ProxyTlsGate.run, which performs the sandbox creation, certificate installation, probing, retrying, and cleanup.

*Call graph*: 2 external calls (__init__, ArgumentParser).
