# Azure App Service Deployment Guide

## Prerequisites

1. An Azure account with an active subscription
2. Azure CLI installed locally
3. Python 3.11 installed

## Deployment Steps

### 1. Create Azure Resources

```bash
# Login to Azure
az login

# Create a resource group
az group create --name llm-pricing-rg --location eastus

# Create an App Service plan
az appservice plan create --name llm-pricing-plan --resource-group llm-pricing-rg --sku B1 --is-linux

# Create a web app
az webapp create --resource-group llm-pricing-rg --plan llm-pricing-plan --name llm-pricing-server --runtime "PYTHON:3.11"
```

### 2. Configure Environment Variables

```bash
# Set environment variables in Azure App Service
az webapp config appsettings set --resource-group llm-pricing-rg --name llm-pricing-server --settings \
  OPENAI_API_KEY="your_openai_api_key" \
  ANTHROPIC_API_KEY="your_anthropic_api_key" \
  SERVER_HOST="0.0.0.0" \
  SERVER_PORT="8000"
```

### 3. Deploy via GitHub Actions

1. Create a service principal for GitHub Actions:
```bash
az ad sp create-for-rbac --name "llm-pricing-github-actions" --role contributor \
  --scopes /subscriptions/{subscription-id}/resourceGroups/llm-pricing-rg \
  --sdk-auth
```

2. Add the output JSON as a GitHub secret named `AZURE_CREDENTIALS`

3. Add your Azure web app name as a GitHub secret named `AZURE_WEBAPP_NAME`

4. Push to the main branch to trigger deployment

### 4. Manual Deployment (Alternative)

```bash
# Deploy using Azure CLI
az webapp up --name llm-pricing-server --resource-group llm-pricing-rg --runtime "PYTHON:3.11"
```

## Post-Deployment

### Verify Deployment

Visit your app at: `https://llm-pricing-server.azurewebsites.net`

### View Logs

```bash
# Stream logs
az webapp log tail --name llm-pricing-server --resource-group llm-pricing-rg

# Download logs
az webapp log download --name llm-pricing-server --resource-group llm-pricing-rg
```

## Troubleshooting

### Common Issues

1. **Application not starting**: Check logs for errors
2. **Dependencies not installed**: Ensure requirements.txt is in the root directory
3. **Environment variables not set**: Verify app settings in Azure Portal

### Health Check

Use the `/health` endpoint to verify the application is running:
```bash
curl https://llm-pricing-server.azurewebsites.net/health
```

## Scaling

```bash
# Scale up (change plan)
az appservice plan update --name llm-pricing-plan --resource-group llm-pricing-rg --sku P1V2

# Scale out (add instances)
az appservice plan update --name llm-pricing-plan --resource-group llm-pricing-rg --number-of-workers 2
```
