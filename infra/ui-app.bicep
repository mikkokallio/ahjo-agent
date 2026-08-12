@description('Name of the App Service.')
param appName string

@description('Deployment location.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Microsoft Entra tenant ID.')
param entraTenantId string

@description('Microsoft Entra application client ID.')
param entraClientId string

@secure()
@description('Microsoft Entra application client secret.')
param entraClientSecret string

@description('Foundry hosted agent Responses endpoint.')
param agentEndpoint string

@description('PDF processor Function App base URL.')
param pdfProcessorUrl string

@secure()
@description('PDF processor Function key.')
param pdfProcessorKey string

@description('Name of the existing Foundry account.')
param aiServicesAccountName string

var planName = 'asp-${appName}'
var appHostname = '${appName}.azurewebsites.net'

module appServicePlan 'br/public:avm/res/web/serverfarm:0.7.0' = {
  params: {
    name: planName
    location: location
    tags: tags
    kind: 'linux'
    reserved: true
    skuName: 'B1'
    skuCapacity: 1
    zoneRedundant: false
  }
}

module webApp 'br/public:avm/res/web/site:0.22.0' = {
  params: {
    name: appName
    location: location
    tags: tags
    kind: 'app,linux'
    serverFarmResourceId: appServicePlan.outputs.resourceId
    httpsOnly: true
    clientAffinityEnabled: false
    publicNetworkAccess: 'Enabled'
    managedIdentities: {
      systemAssigned: true
    }
    basicPublishingCredentialsPolicies: [
      {
        name: 'ftp'
        allow: false
      }
      {
        name: 'scm'
        allow: false
      }
    ]
    siteConfig: {
      alwaysOn: true
      appCommandLine: 'python -m uvicorn app:app --host 0.0.0.0 --port 8000'
      ftpsState: 'Disabled'
      http20Enabled: true
      linuxFxVersion: 'PYTHON|3.13'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
    }
    configs: [
      {
        name: 'appsettings'
        properties: {
          AGENT_ENDPOINT: agentEndpoint
          CACHE_DB_PATH: '/home/data/cache.db'
          MICROSOFT_PROVIDER_AUTHENTICATION_SECRET: entraClientSecret
          PDF_PROCESSOR_KEY: pdfProcessorKey
          PDF_PROCESSOR_URL: pdfProcessorUrl
          SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
          WEBSITES_ENABLE_APP_SERVICE_STORAGE: 'true'
          WEBSITES_PORT: '8000'
        }
      }
    ]
  }
}

resource app 'Microsoft.Web/sites@2024-11-01' existing = {
  name: appName
}

resource authSettings 'Microsoft.Web/sites/config@2024-11-01' = {
  parent: app
  name: 'authsettingsV2'
  properties: {
    globalValidation: {
      requireAuthentication: true
      redirectToProvider: 'azureactivedirectory'
      unauthenticatedClientAction: 'RedirectToLoginPage'
    }
    httpSettings: {
      requireHttps: true
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          clientSecretSettingName: 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET'
          openIdIssuer: '${environment().authentication.loginEndpoint}${entraTenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            entraClientId
            'api://${entraClientId}'
            'https://${appHostname}'
          ]
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
    }
    platform: {
      enabled: true
      runtimeVersion: '~1'
    }
  }
  dependsOn: [webApp]
}

resource aiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: aiServicesAccountName
}

resource aiUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiAccount.id, appName, 'Azure AI User')
  scope: aiAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
    principalId: webApp.outputs.systemAssignedMIPrincipalId!
    principalType: 'ServicePrincipal'
  }
}

output name string = webApp.outputs.name
output url string = 'https://${appHostname}'
