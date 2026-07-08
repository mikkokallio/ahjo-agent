// Flex Consumption Function App for PDF processing
// Uses identity-based storage (no shared key access required)

param location string = resourceGroup().location
param tags object = {}
param functionAppName string
param storageAccountName string
param appInsightsConnectionString string = ''

// Storage account (shared key disabled by policy - uses identity-based access)
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// Deployment container for Flex Consumption
var deploymentContainerName = 'app-package-${take(functionAppName, 32)}'

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {}
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: deploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

// User-assigned managed identity for the function app
resource functionIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${functionAppName}'
  location: location
  tags: tags
}

// RBAC: Storage Blob Data Owner on the function's own storage
resource storageBlobDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionIdentity.id, 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC: Storage Blob Data Contributor on the cache storage account (stahjoagentcache)
resource cacheStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: 'stahjoagentcache'
}

resource cacheBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cacheStorage.id, functionIdentity.id, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  scope: cacheStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC: Cognitive Services User on the AI account (for LLM summarization)
resource aiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: 'ai-account-mapx4sxw6thvg'
}

resource cogServicesUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiAccount.id, functionIdentity.id, 'a97b65f3-24c7-4388-baec-2e87135dc908')
  scope: aiAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Flex Consumption plan
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'plan-${functionAppName}'
  location: location
  tags: tags
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

// Function App
resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  tags: union(tags, { 'azd-service-name': 'pdf-processor' })
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${functionIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: functionIdentity.id
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    keyVaultReferenceIdentity: functionIdentity.id
    siteConfig: {
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: functionIdentity.properties.clientId }
        { name: 'AZURE_CLIENT_ID', value: functionIdentity.properties.clientId }
        { name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME', value: 'gpt-5.4' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
      ]
    }
  }
  dependsOn: [storageBlobDataOwner]
}

output functionAppName string = functionApp.name
output functionAppHostName string = functionApp.properties.defaultHostName
output identityPrincipalId string = functionIdentity.properties.principalId
output identityClientId string = functionIdentity.properties.clientId
