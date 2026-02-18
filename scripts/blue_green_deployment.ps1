# Blue-Green Deployment Helper for Azure App Service (PowerShell)
# Supports automated slot management, health checks, and rollback

param(
    [Parameter(Position = 0)]
    [ValidateSet('check', 'deploy', 'status', 'history', 'rollback', 'swap', 'health', 'test')]
    [string]$Command = 'deploy',
    
    [Parameter(Position = 1)]
    [string]$SlotName = 'staging',
    
    [string]$ResourceGroup = 'llm-pricing-rg',
    [string]$AppName = 'llm-pricing-api',
    [int]$HealthCheckTimeout = 60,
    [int]$WaitBetweenChecks = 5
)

# Configuration
$StagingSlot = 'staging'
$HealthCheckUrl = '/health'
$ModelsUrl = '/models'
$PricingUrl = '/pricing'

# Helper functions
function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Blue
}

function Write-Success {
    param([string]$Message)
    Write-Host "[✓] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[⚠] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[✗] $Message" -ForegroundColor Red
}

# Check prerequisites
function Check-Prerequisites {
    Write-Info "Checking prerequisites..."
    
    $az = Get-Command az -ErrorAction SilentlyContinue
    $curl = Get-Command curl -ErrorAction SilentlyContinue
    
    if (-not $az) {
        Write-Error "Azure CLI not found"
        exit 1
    }
    
    if (-not $curl) {
        Write-Error "curl not found"
        exit 1
    }
    
    Write-Success "Prerequisites met: Azure CLI, curl available"
}

# Get slot URL
function Get-SlotUrl {
    param([string]$Slot)
    
    if ($Slot -eq 'production') {
        return "https://$AppName.azurewebsites.net"
    }
    else {
        return "https://$AppName-$Slot.azurewebsites.net"
    }
}

# Health check for a slot
function Test-SlotHealth {
    param([string]$Slot)
    
    $url = Get-SlotUrl $Slot
    $elapsed = 0
    
    Write-Info "Performing health check on $Slot slot..."
    
    while ($elapsed -lt $HealthCheckTimeout) {
        try {
            $response = curl.exe -s -o $null -w "%{http_code}" "$url$HealthCheckUrl" 2>$null
            $httpCode = $response.Trim()
            
            if ($httpCode -eq '200') {
                Write-Success "$Slot slot is healthy (HTTP $httpCode)"
                return $true
            }
            
            Write-Warning "$Slot slot returned HTTP $httpCode, waiting... ($elapsed/${HealthCheckTimeout}s)"
        }
        catch {
            Write-Warning "Health check error: $_"
        }
        
        Start-Sleep -Seconds $WaitBetweenChecks
        $elapsed += $WaitBetweenChecks
    }
    
    Write-Error "$Slot slot failed health check after ${HealthCheckTimeout}s"
    return $false
}

# Functional test for a slot
function Test-SlotFunctional {
    param([string]$Slot)
    
    $url = Get-SlotUrl $Slot
    
    Write-Info "Running functional tests on $Slot slot..."
    
    try {
        # Test /models endpoint
        $modelsResponse = curl.exe -s "$url$ModelsUrl" 2>$null | ConvertFrom-Json
        $totalModels = $modelsResponse.total_models
        
        if ($totalModels -gt 0) {
            Write-Success "Models endpoint working: $totalModels models available"
        }
        else {
            Write-Error "Models endpoint returned invalid data"
            return $false
        }
        
        # Test /pricing endpoint
        $pricingResponse = curl.exe -s "$url$PricingUrl" 2>$null | ConvertFrom-Json
        if ($pricingResponse.models.Count -gt 0) {
            Write-Success "Pricing endpoint working: $($pricingResponse.models.Count) models have pricing"
        }
        else {
            Write-Error "Pricing endpoint failed"
            return $false
        }
        
        return $true
    }
    catch {
        Write-Error "Functional test error: $_"
        return $false
    }
}

# Swap slots (blue-green deployment)
function Invoke-SlotSwap {
    Write-Info "Swapping $StagingSlot slot to production..."
    
    try {
        az webapp deployment slot swap `
            --resource-group $ResourceGroup `
            --name $AppName `
            --slot $StagingSlot
        
        Write-Success "Slot swap completed"
        return $true
    }
    catch {
        Write-Error "Failed to swap slots: $_"
        return $false
    }
}

# Rollback to previous slot
function Invoke-SlotRollback {
    Write-Warning "Rolling back: swapping slots back..."
    
    try {
        az webapp deployment slot swap `
            --resource-group $ResourceGroup `
            --name $AppName `
            --slot $StagingSlot
        
        Write-Success "Rollback completed"
        return $true
    }
    catch {
        Write-Error "Failed to rollback slots: $_"
        return $false
    }
}

# Main deployment function
function Invoke-BlueGreenDeploy {
    Write-Info "Starting blue-green deployment..."
    
    # Wait for slot to be ready
    Write-Info "Waiting 30 seconds for slot startup..."
    Start-Sleep -Seconds 30
    
    # Health check staging
    if (-not (Test-SlotHealth $StagingSlot)) {
        Write-Error "Staging slot health check failed, aborting deployment"
        return $false
    }
    
    # Functional tests on staging
    if (-not (Test-SlotFunctional $StagingSlot)) {
        Write-Error "Staging slot functional tests failed, aborting deployment"
        return $false
    }
    
    # Swap to production
    if (-not (Invoke-SlotSwap)) {
        Write-Error "Slot swap failed, attempting rollback..."
        Invoke-SlotRollback
        return $false
    }
    
    # Verify production
    if (-not (Test-SlotHealth 'production')) {
        Write-Warning "Production health check failed, but swap completed"
        Write-Warning "Monitor the application and consider rolling back if issues persist"
        return $true
    }
    
    Write-Success "Blue-green deployment completed successfully!"
    return $true
}

# Get current slots status
function Get-DeploymentStatus {
    Write-Info "Deployment slots status:"
    
    az webapp deployment slot list `
        --resource-group $ResourceGroup `
        --name $AppName `
        --output table
}

# Main
switch ($Command) {
    'check' {
        Check-Prerequisites
    }
    'deploy' {
        Check-Prerequisites
        Invoke-BlueGreenDeploy
    }
    'status' {
        Check-Prerequisites
        Get-DeploymentStatus
    }
    'history' {
        Check-Prerequisites
        Write-Info "Recent deployments:"
        az webapp deployment slot list `
            --resource-group $ResourceGroup `
            --name $AppName `
            --output json | ConvertFrom-Json
    }
    'rollback' {
        Check-Prerequisites
        Invoke-SlotRollback
    }
    'swap' {
        Check-Prerequisites
        Invoke-SlotSwap
    }
    'health' {
        Check-Prerequisites
        Test-SlotHealth $SlotName
    }
    'test' {
        Check-Prerequisites
        Test-SlotFunctional $SlotName
    }
    default {
        Write-Host "Blue-Green Deployment Helper" -ForegroundColor Cyan
        Write-Host "Usage: .\blue_green_deployment.ps1 [command] [options]"
        Write-Host ""
        Write-Host "Commands:" -ForegroundColor Green
        Write-Host "  check               - Check prerequisites"
        Write-Host "  deploy              - Full blue-green deployment workflow"
        Write-Host "  status              - Show deployment slots status"
        Write-Host "  history             - Show recent deployments"
        Write-Host "  rollback            - Rollback to previous slot"
        Write-Host "  swap                - Swap staging to production"
        Write-Host "  health [slot]       - Run health check on slot (default: staging)"
        Write-Host "  test [slot]         - Run functional tests on slot (default: staging)"
    }
}
