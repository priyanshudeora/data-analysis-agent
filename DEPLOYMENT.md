# Deploy InsightPilot to AWS

The production path is:

```text
GitHub push → GitHub Actions tests → Docker image → Amazon ECR
            → AWS Systems Manager → EC2 → HTTPS Application Load Balancer
```

Visitors supply their own OpenRouter API keys in the browser. The deployment does not need an owner model key, and no dataset is included in the container image. Uploaded SQLite, CSV, and ZIP files live only in each Streamlit session's temporary filesystem.

## What is included

- `Dockerfile` builds a non-root image with a Streamlit health check.
- `compose.yaml` runs the same image locally with a read-only filesystem and temporary upload storage.
- `.github/workflows/aws-deploy.yml` tests every change, publishes immutable commit-and-run-addressed images to ECR, and deploys them with short-lived GitHub OIDC credentials.
- `infra/aws-ec2.yaml` creates ECR, an EC2 instance, an HTTPS Application Load Balancer, least-privilege instance and deployment roles, and an SSM deployment document.

## 1. Prepare AWS

You need an AWS account with:

1. A VPC and two public subnets in different Availability Zones.
2. A validated AWS Certificate Manager certificate in the deployment region for your app hostname.
3. The GitHub Actions OIDC provider `https://token.actions.githubusercontent.com` in IAM, with audience `sts.amazonaws.com`. This is created once per AWS account.
4. AWS CLI credentials allowed to create the CloudFormation stack.

The EC2 instance has no inbound SSH rule. GitHub deploys through Systems Manager, and the app server accepts traffic only from the load balancer.

Deploy the stack, replacing the example values:

```bash
aws cloudformation deploy \
  --stack-name insightpilot-production \
  --template-file infra/aws-ec2.yaml \
  --capabilities CAPABILITY_IAM \
  --region ap-south-1 \
  --parameter-overrides \
    VpcId=vpc-0123456789abcdef0 \
    PublicSubnetA=subnet-0123456789abcdef0 \
    PublicSubnetB=subnet-0fedcba9876543210 \
    CertificateArn=arn:aws:acm:ap-south-1:123456789012:certificate/REPLACE_ME \
    GitHubOidcProviderArn=arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com \
    GitHubSubject=repo:priyanshudeora/data-analysis-agent:environment:production
```

GitHub repositories using immutable OIDC subject claims include owner and repository IDs in `GitHubSubject`. Use the exact `sub` format shown for your repository in GitHub's OIDC documentation instead of the default name-based value.

Read the values needed by GitHub Actions:

```bash
aws cloudformation describe-stacks \
  --stack-name insightpilot-production \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs' \
  --output table
```

## 2. Configure GitHub

Create a GitHub environment named `production`. Add branch protection or required reviewers if desired. Add these **repository-level Actions variables** so the workflow can decide whether AWS deployment is configured before it starts the protected environment job:

- `AWS_REGION`: the stack region, such as `ap-south-1`
- `AWS_ROLE_ARN`: the `GitHubRoleArn` stack output
- `ECR_REPOSITORY`: the `EcrRepositoryName` stack output
- `EC2_INSTANCE_ID`: the `Ec2InstanceId` stack output
- `SSM_DEPLOY_DOCUMENT`: the `SsmDeployDocument` stack output

No AWS access-key secret is required. The IAM trust policy accepts tokens only from this repository's `production` GitHub environment.

The workflow deploys pushes to `codex/openrouter-deployment` and can also be started manually from the Actions page. Pull requests run tests and build the container without receiving AWS permissions.

## 3. Connect the hostname

Create a DNS record for the hostname covered by your ACM certificate:

- Route 53: create an alias record pointing to the `LoadBalancerDnsName` output.
- External DNS: create a CNAME pointing to `LoadBalancerDnsName`.

HTTP requests redirect to HTTPS. Do not ask visitors to enter API keys through a plain HTTP endpoint.

The first stack creation leaves the load-balancer target unhealthy until the first GitHub deployment starts the container. After the workflow succeeds, verify:

```text
https://your-app-hostname/_stcore/health
```

## Local Docker verification

```bash
docker compose up --build
```

Open `http://localhost:8501`. Stop it with:

```bash
docker compose down
```

## Application behavior

- Every visitor must upload a SQLite database, CSV file, or ZIP containing supported data.
- Each browser session gets a separate temporary directory. Data disappears when the container or session is removed.
- Questions, schema, sampled query results, and findings are sent to OpenRouter and the selected provider after the visitor starts an analysis.
- Visitor keys remain in Streamlit session memory and are not written to the image, ECR, GitHub, environment variables, or disk.
- The EC2 container runs with dropped Linux capabilities, a read-only root filesystem, and a size-limited writable `/tmp` mount.
- ECR scans images on push and keeps the newest 20 images.

## Tests run in CI

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m tests.smoke_nodes
python -m tests.smoke_llm_nodes
docker build --tag insightpilot:test .
```

AWS resources in this stack incur charges, especially the Application Load Balancer, EC2 instance, public IPv4 address, ECR storage, and data transfer. Delete the CloudFormation stack when it is no longer needed.

References: [GitHub OIDC for AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws), [AWS Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html), and [Amazon ECR](https://docs.aws.amazon.com/AmazonECR/latest/userguide/what-is-ecr.html).
