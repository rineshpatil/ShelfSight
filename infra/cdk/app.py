#!/usr/bin/env python
"""CDK app for ShelfSight. Deploy: uv run --group infra cdk deploy (from infra/cdk)."""
import os

import aws_cdk as cdk

from shelfsight_stack import ShelfSightStack

app = cdk.App()
ShelfSightStack(
    app,
    "ShelfSight",
    instance_type=app.node.try_get_context("instance_type") or "t4g.small",
    env=cdk.Environment(account=os.environ["CDK_DEFAULT_ACCOUNT"], region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1")),
    description="ShelfSight AEO diagnosis: EC2 (n8n + SearxNG + bridge), S3 lake, Bedrock access",
)
app.synth()
