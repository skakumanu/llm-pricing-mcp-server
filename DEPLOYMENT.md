# Azure App Service Deployment Guide

This guide covers deploying the LLM Pricing MCP Server to Azure App Service.

## Prerequisites

1. An Azure account with an active subscription
2. Azure CLI installed locally
3. Python 3.11 installed
4. Git installed

## Deployment Steps

### 1. Create Azure Resources

```bash
# Login to Azure
az login

# Set variables for your deployment
RESOURCE_GROUP="llm-pricing-rg-westus2"
LOCATION="westus2"
APP_SERVICE_PLAN="llm-pricing-plan"
WEB_APP_NAME="llm-pricing-mcp"

# Create a resource group
az group create --name $RESOURCE_GROUP --location $LOCATION

# Create an App Service plan (Linux) 
az appservice plan create --name $APP_SERVICE_PLAN --resource-group $RESOURCE_GROUP --sku B1 --is-linux

# Create a web app
az webapp create --resource-group $RESOURCE_GROUP --plan $APP_SERVICE_PLAN --name $WEB_APP_NAME --runtime "PYTHON:3.11"
```

### 2. Configure Environment Variables

```bash
# Set environment variables in Azure App Service
az webapp config appsettings set --resource-group $RESOURCE_GROUP --name $WEB_APP_NAME --settings \
  OPENAI_API_KEY="your_openai_api_key" \
  ANTHROPIC_API_KEY="your_anthropic_api_key" \
  MCP_API_KEY="replace-with-strong-key" \
  MCP_API_KEY_HEADER="x-api-key" \
  MAX_BODY_BYTES="1000000" \
  RATE_LIMIT_PER_MINUTE="60" \
  SERVER_HOST="0.0.0.0" \
  SERVER_PORT="8000" \
  DEBUG="false"
```

### 3. Deploy via Zip Deployment (Recommended)

The application uses a startup script (`run.sh`) to install dependencies and start the server:

```bash
# Create deployment package
python -c "
import zipfile
from pathlib import Path

zip_path = Path('deploy_linux.zip')
if zip_path.exists():
    zip_path.unlink()

files = ['src', 'requirements.txt', 'Procfile', 'runtime.txt', 'run.sh']

def should_skip(p):
    return '__pycache__' in p.parts or p.suffix == '.pyc'

with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for item in files:
        p = Path(item)
        if p.is_dir():
            for f in p.rglob('*'):
                if f.is_file() and not should_skip(f):
                    zf.write(f, f.as_posix())
        elif p.is_file():
            zf.write(p, p.as_posix())

print(f'Created {zip_path.name}')
"

# Deploy the zip file
az webapp deployment source config-zip --resource-group $RESOURCE_GROUP --name $WEB_APP_NAME --src deploy_linux.zip
```

**Important**: The `run.sh` script must have Unix (LF) line endings. Use `.gitattributes` with `*.sh text eol=lf` to ensure correct line endings in git.

### 4. Manual Deployment via Git (Alternative)

```bash
# Configure git deployment
az webapp deployment user set --user-name <username> --password <password>

az webapp deployment source config-local-git --resource-group $RESOURCE_GROUP --name $WEB_APP_NAME

# Add Azure remote and push
git remote add azure <git-url-from-previous-output>
git push azure master
```

## Post-Deployment

### Verify Deployment

Wait 1-2 minutes for the app to start, then visit your app:
```
https://$WEB_APP_NAME.azurewebsites.net
```

For the llm-pricing-mcp app:
```
https://llm-pricing-mcp.azurewebsites.net
```

### Test Endpoints

```bash
# Health check
curl https://llm-pricing-mcp.azurewebsites.net/health

# Get available models
curl https://llm-pricing-mcp.azurewebsites.net/models

# Get pricing data
curl https://llm-pricing-mcp.azurewebsites.net/pricing

# Get performance metrics
curl https://llm-pricing-mcp.azurewebsites.net/performance
```

### View Logs

```bash
# Stream logs in real-time
az webapp log tail --resource-group $RESOURCE_GROUP --name $WEB_APP_NAME

# Download logs for later analysis
az webapp log download --resource-group $RESOURCE_GROUP --name $WEB_APP_NAME --log-file logs.zip
```

## Troubleshooting

### Common Issues

1. **Application not starting (timeout error)**
   - Check logs: `az webapp log tail --resource-group $RESOURCE_GROUP --name $WEB_APP_NAME`
   - Verify `run.sh` has Unix (LF) line endings, not Windows (CRLF)
   - Ensure all dependencies in `requirements.txt` are compatible with Python 3.11

2. **Dependencies not installed**
   - The `run.sh` script automatically installs dependencies from `requirements.txt`
   - Verify `requirements.txt` exists in the root directory of the deployment package

3. **Environment variables not set**
   - Verify app settings in Azure Portal: App Service > Configuration > Application settings
   - Restart the app after changing settings: `az webapp restart --resource-group $RESOURCE_GROUP --name $WEB_APP_NAME`

4. **File not found errors in logs**
   - Ensure deployment zip includes all necessary files: `src/`, `requirements.txt`, `Procfile`, `runtime.txt`, `run.sh`
   - Recreate the deployment zip and redeploy if files are missing

### Health Check

Use the `/health` endpoint to verify the application is running:
```bash
curl https://llm-pricing-mcp.azurewebsites.net/health

# Should return:
# {"status":"healthy","service":"LLM Pricing MCP Server","version":"1.4.2"}
```

## Scaling

```bash
# Scale up (change plan to higher tier)
az appservice plan update --name $APP_SERVICE_PLAN --resource-group $RESOURCE_GROUP --sku P1V2

# Scale out (add instances for load balancing)
az appservice plan update --name $APP_SERVICE_PLAN --resource-group $RESOURCE_GROUP --number-of-workers 2
```

## Restart the Application

```bash
# Restart the web app
az webapp restart --resource-group $RESOURCE_GROUP --name $WEB_APP_NAME
```
