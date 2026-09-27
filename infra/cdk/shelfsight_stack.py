"""One stack: S3 lake, ECR image, and an EC2 host running n8n, SearxNG and the ShelfSight bridge.

No inbound ports. Reach n8n with SSM port forwarding:
  aws ssm start-session --target <instance-id> --document-name AWS-StartPortForwardingSession \
      --parameters '{"portNumber":["5678"],"localPortNumber":["5679"]}'

Secrets live in SSM Parameter Store as SecureStrings under /shelfsight/ and are never in this code.
"""
from pathlib import Path

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
PARAM_PREFIX = "/shelfsight"
COMPOSE_VERSION = "v2.40.3"


class ShelfSightStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, instance_type: str, **kwargs):
        super().__init__(scope, cid, **kwargs)

        lake = s3.Bucket(
            self, "Lake",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=False,
            removal_policy=RemovalPolicy.RETAIN,  # the time series is the product; never auto-delete it
        )

        image = ecr_assets.DockerImageAsset(
            self, "PipelineImage",
            directory=str(REPO_ROOT),
            platform=ecr_assets.Platform.LINUX_ARM64,
            exclude=[".git", ".venv", "data/lake", "docs", "tests"],
        )

        vpc = ec2.Vpc(  # public subnet only: a NAT gateway would cost more than the instance
            self, "Vpc",
            max_azs=1,
            nat_gateways=0,
            subnet_configuration=[ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24)],
        )
        sg = ec2.SecurityGroup(self, "HostSg", vpc=vpc, allow_all_outbound=True,
                               description="ShelfSight host: no inbound; access via SSM Session Manager")

        role = iam.Role(self, "HostRole", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                        managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")])
        lake.grant_read_write(role)
        image.repository.grant_pull(role)
        role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.amazon.nova-*",
                       "arn:aws:bedrock:*::foundation-model/amazon.nova-*"]))
        role.add_to_policy(iam.PolicyStatement(  # nova_grounding is a system tool, not a model
            actions=["bedrock:InvokeTool"],
            resources=[f"arn:aws:bedrock:{self.region}:{self.account}:system-tool/amazon.nova_grounding",
                       "arn:aws:bedrock::" + self.account + ":system-tool/amazon.nova_grounding"]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter", "ssm:GetParameters"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{PARAM_PREFIX}/*"]))

        compose = (Path(__file__).parent / "host-compose.yml").read_text(encoding="utf-8")
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euxo pipefail",
            "dnf -y update",
            "dnf -y install docker",
            "systemctl enable --now docker",
            "mkdir -p /usr/libexec/docker/cli-plugins /opt/shelfsight",
            f"curl -fsSL https://github.com/docker/compose/releases/download/{COMPOSE_VERSION}/docker-compose-linux-aarch64"
            " -o /usr/libexec/docker/cli-plugins/docker-compose",
            "chmod +x /usr/libexec/docker/cli-plugins/docker-compose",
            f"cat > /opt/shelfsight/docker-compose.yml <<'COMPOSE_EOF'\n{compose}\nCOMPOSE_EOF",
            "cat > /opt/shelfsight/searxng-settings.yml <<'SEARX_EOF'\n"
            "use_default_settings: true\n"
            "server:\n  secret_key: \"set-by-env\"\n  limiter: false\n  image_proxy: false\n"
            "search:\n  formats: [html, json]\n"
            "SEARX_EOF",
            f"cat > /opt/shelfsight/.env <<EOF\n"
            f"SHELFSIGHT_IMAGE={image.image_uri}\n"
            f"SHELFSIGHT_LAKE=s3://{lake.bucket_name}/lake\n"
            f"AWS_DEFAULT_REGION={self.region}\n"
            f"SHELFSIGHT_BRIDGE_TOKEN=$(aws ssm get-parameter --with-decryption --name {PARAM_PREFIX}/bridge_token"
            f" --query Parameter.Value --output text --region {self.region} 2>/dev/null || echo '')\n"
            f"GEMINI_API_KEY=$(aws ssm get-parameter --with-decryption --name {PARAM_PREFIX}/gemini_api_key"
            f" --query Parameter.Value --output text --region {self.region} 2>/dev/null || echo '')\n"
            f"SEARXNG_SECRET=$(openssl rand -hex 32)\nEOF",
            "chmod 600 /opt/shelfsight/.env",
            f"aws ecr get-login-password --region {self.region} | docker login --username AWS --password-stdin "
            f"{self.account}.dkr.ecr.{self.region}.amazonaws.com",
            "cd /opt/shelfsight && docker compose up -d",
        )

        host = ec2.Instance(
            self, "Host",
            vpc=vpc,
            instance_type=ec2.InstanceType(instance_type),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(cpu_type=ec2.AmazonLinuxCpuType.ARM_64),
            security_group=sg,
            role=role,
            user_data=user_data,
            block_devices=[ec2.BlockDevice(device_name="/dev/xvda",
                                           volume=ec2.BlockDeviceVolume.ebs(30, encrypted=True,
                                                                            volume_type=ec2.EbsDeviceVolumeType.GP3))],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            user_data_causes_replacement=True,
        )
        eip = ec2.CfnEIP(self, "HostIp", domain="vpc", instance_id=host.instance_id)

        CfnOutput(self, "BucketName", value=lake.bucket_name)
        CfnOutput(self, "InstanceId", value=host.instance_id)
        CfnOutput(self, "PublicIp", value=eip.ref, description="stable IP the SearxNG probe searches from")
        CfnOutput(self, "ImageUri", value=image.image_uri)
        CfnOutput(self, "N8nPortForward",
                  value=f"aws ssm start-session --target {host.instance_id} --document-name "
                        "AWS-StartPortForwardingSession --parameters "
                        "'{\"portNumber\":[\"5678\"],\"localPortNumber\":[\"5679\"]}'")
