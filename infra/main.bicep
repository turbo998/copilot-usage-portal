// =============================================================================
// Copilot Usage Portal — main.bicep
// Resource group-scoped deployment. Provisions:
//   - Log Analytics workspace + Application Insights
//   - Storage account (Functions runtime backend)
//   - Cosmos DB (Free Tier, NoSQL) with database + 2 containers
//   - Linux Consumption Function App (Python 3.11) with system MI
//   - Static Web App (Standard) linked to the Function App
//   - RBAC: Cosmos DB Data Contributor for the Function App MI
// =============================================================================

@description('Project short name (used for naming).')
param projectName string = 'copilotusage'

@description('Environment short tag, e.g. dev, prod.')
param env string = 'dev'

@description('Azure region for all resources.')
param location string = 'southeastasia'

@description('Region for Static Web App. SWA Standard is not in southeastasia; use East Asia.')
param swaLocation string = 'eastasia'

@description('Pre-shared secret expected in the X-Collector-Key header from collectors.')
@secure()
param collectorIngestKey string

@description('TTL (seconds) for documents in usage_events container. 0 = infinite.')
param eventsTtlSeconds int = 31536000 // 365 days

@description('Cosmos DB capacity mode. "serverless" pays per request (best for self-only usage). "provisioned" reserves 1000 RU/s shared across the DB.')
@allowed([
  'serverless'
  'provisioned'
])
param cosmosCapacityMode string = 'serverless'

@description('Enable Cosmos DB Free Tier. NOT supported on internal Microsoft subscriptions.')
param cosmosFreeTier bool = false

@description('CIDR for the workload VNet (must not collide with any peered network).')
param vnetAddressSpace string = '10.30.0.0/16'

@description('Subnet for Function App regional VNet integration (egress only). Delegated to Microsoft.App/environments. Needs >= /26 to be safe.')
param funcSubnetCidr string = '10.30.1.0/24'

@description('Subnet hosting Private Endpoints for Storage and Cosmos. Small subnet is fine; /28 supports up to 11 PEs.')
param peSubnetCidr string = '10.30.2.0/27'

@description('Tags applied to all resources.')
param tags object = {
  project: 'copilot-usage-portal'
  env: env
}

// -----------------------------------------------------------------------------
// Naming
// -----------------------------------------------------------------------------
var rgSuffix = uniqueString(resourceGroup().id)
var shortSuffix = substring(rgSuffix, 0, 6)
var lawName     = toLower('log-${projectName}-${env}-${shortSuffix}')
var aiName      = toLower('ai-${projectName}-${env}-${shortSuffix}')
var storageName = toLower('st${projectName}${env}${shortSuffix}')
var cosmosName  = toLower('cosmos-${projectName}-${env}-${shortSuffix}')
var planName    = toLower('plan-${projectName}-${env}-${shortSuffix}')
var funcName    = toLower('func-${projectName}-${env}-${shortSuffix}')
var swaName     = toLower('stapp-${projectName}-${env}-${shortSuffix}')
var vnetName        = toLower('vnet-${projectName}-${env}-${shortSuffix}')
var funcSubnetName  = 'snet-func-integration'
var peSubnetName    = 'snet-pe'

// -----------------------------------------------------------------------------
// Log Analytics + App Insights
// -----------------------------------------------------------------------------
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: aiName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

// -----------------------------------------------------------------------------
// VNet for Function App egress + Private Endpoints for Storage/Cosmos
// MCAPS policy resets publicNetworkAccess to Disabled on data services; this
// VNet + PE topology lets the Function App keep working regardless.
// -----------------------------------------------------------------------------
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressSpace
      ]
    }
    subnets: [
      {
        // Regional VNet integration target for the Flex Consumption Function App.
        // Flex Consumption requires the subnet to be delegated to
        // 'Microsoft.App/environments'. The subnet must be empty before
        // delegation, and cannot be shared with PEs / NICs.
        name: funcSubnetName
        properties: {
          addressPrefix: funcSubnetCidr
          delegations: [
            {
              name: 'flex-consumption-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
      {
        // Dedicated subnet for Private Endpoints (Storage blob/queue + Cosmos).
        // PE network policies must be disabled for the NICs to be created.
        name: peSubnetName
        properties: {
          addressPrefix: peSubnetCidr
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
    ]
  }
}

resource funcSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' existing = {
  parent: vnet
  name: funcSubnetName
}

resource peSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' existing = {
  parent: vnet
  name: peSubnetName
}

// Private DNS zones (privatelink.* — global resources, location must be 'global')
// They are linked to the VNet so the Function App resolves
// *.blob.core.windows.net / *.documents.azure.com to the PE's private IP.
resource dnsZoneBlob 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource dnsZoneQueue 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.queue.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource dnsZoneCosmos 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.documents.azure.com'
  location: 'global'
  tags: tags
}

resource dnsLinkBlob 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dnsZoneBlob
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

resource dnsLinkQueue 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dnsZoneQueue
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

resource dnsLinkCosmos 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dnsZoneCosmos
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

// -----------------------------------------------------------------------------
// Storage account (Functions deployment + AzureWebJobs, identity-based)
// -----------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'app-package-${toLower(funcName)}'
  properties: {
    publicAccess: 'None'
  }
}

// -----------------------------------------------------------------------------
// -----------------------------------------------------------------------------
// Cosmos DB (NoSQL). Defaults to Serverless. Free Tier disabled by default
// (not supported on internal Microsoft subscriptions).
// -----------------------------------------------------------------------------
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: cosmosFreeTier
    enableAutomaticFailover: false
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: cosmosCapacityMode == 'serverless' ? [
      {
        name: 'EnableServerless'
      }
    ] : []
    publicNetworkAccess: 'Enabled'
    backupPolicy: {
      type: 'Periodic'
      periodicModeProperties: {
        backupIntervalInMinutes: 1440
        backupRetentionIntervalInHours: 48
        backupStorageRedundancy: 'Local'
      }
    }
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'copilot_usage'
  properties: {
    resource: {
      id: 'copilot_usage'
    }
    options: cosmosCapacityMode == 'provisioned' ? {
      throughput: 1000 // shared across containers
    } : {}
  }
}

resource usageEventsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'usage_events'
  properties: {
    resource: {
      id: 'usage_events'
      partitionKey: {
        paths: [
          '/client'
        ]
        kind: 'Hash'
      }
      defaultTtl: eventsTtlSeconds
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/_etag/?' }
        ]
        compositeIndexes: [
          [
            { path: '/date', order: 'ascending' }
            { path: '/client', order: 'ascending' }
          ]
          [
            { path: '/model', order: 'ascending' }
            { path: '/date', order: 'ascending' }
          ]
        ]
      }
    }
  }
}

resource costTableContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'cost_table'
  properties: {
    resource: {
      id: 'cost_table'
      partitionKey: {
        paths: [
          '/id'
        ]
        kind: 'Hash'
      }
    }
  }
}

// -----------------------------------------------------------------------------
// Flex Consumption plan + Function App (identity-based deployment storage)
// -----------------------------------------------------------------------------
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource funcApp 'Microsoft.Web/sites@2023-12-01' = {
  name: funcName
  location: location
  kind: 'functionapp,linux'
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    // Regional VNet integration: outbound traffic from the Function App
    // egresses through snet-func-integration so it hits the Storage/Cosmos
    // private endpoints via the linked private DNS zones.
    virtualNetworkSubnetId: funcSubnet.id
    vnetRouteAllEnabled: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deploymentContainer.name}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
    }
    siteConfig: {
      ftpsState: 'FtpsOnly'
      minTlsVersion: '1.2'
      cors: {
        allowedOrigins: [
          'https://portal.azure.com'
        ]
        supportCredentials: false
      }
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName',   value: storage.name }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'COSMOS_ENDPOINT',                    value: cosmos.properties.documentEndpoint }
        { name: 'COSMOS_DATABASE',                    value: cosmosDb.name }
        { name: 'COSMOS_EVENTS_CONTAINER',            value: usageEventsContainer.name }
        { name: 'COSMOS_COST_CONTAINER',              value: costTableContainer.name }
        { name: 'INGEST_KEY',                         value: collectorIngestKey }
        { name: 'PYTHON_ENABLE_WORKER_EXTENSIONS',    value: '1' }
      ]
    }
  }
}

// Storage Blob Data Owner: function MI needs to read/write deployment package + AzureWebJobs blobs
var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'

resource storageBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, funcApp.id, 'storage-blob-data-owner')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
    principalId: funcApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// -----------------------------------------------------------------------------
// Cosmos DB Built-in Data Contributor role assignment for Function App MI
// (data-plane RBAC — uses Cosmos DB sqlRoleAssignments resource)
// -----------------------------------------------------------------------------
var cosmosBuiltInDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource cosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, funcApp.id, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosBuiltInDataContributorRoleId}'
    principalId: funcApp.identity.principalId
    scope: cosmos.id
  }
}

// -----------------------------------------------------------------------------
// Private Endpoints — Storage (blob + queue) and Cosmos (sql)
// Each PE lives in snet-pe, and has a dnsZoneGroup that auto-registers an A
// record in the matching private DNS zone for the VNet to resolve.
// -----------------------------------------------------------------------------
resource peStorageBlob 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: 'pe-${storageName}-blob'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'blob'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

resource peStorageBlobDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peStorageBlob
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: {
          privateDnsZoneId: dnsZoneBlob.id
        }
      }
    ]
  }
}

resource peStorageQueue 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: 'pe-${storageName}-queue'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'queue'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: [
            'queue'
          ]
        }
      }
    ]
  }
}

resource peStorageQueueDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peStorageQueue
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'queue'
        properties: {
          privateDnsZoneId: dnsZoneQueue.id
        }
      }
    ]
  }
}

resource peCosmosSql 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: 'pe-${cosmosName}-sql'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'sql'
        properties: {
          privateLinkServiceId: cosmos.id
          groupIds: [
            'Sql'
          ]
        }
      }
    ]
  }
}

resource peCosmosSqlDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: peCosmosSql
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'cosmos'
        properties: {
          privateDnsZoneId: dnsZoneCosmos.id
        }
      }
    ]
  }
}

// -----------------------------------------------------------------------------
// Static Web App (Standard) — linked to the Function App
// -----------------------------------------------------------------------------
resource swa 'Microsoft.Web/staticSites@2023-12-01' = {
  name: swaName
  location: swaLocation
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Disabled'
  }
}

// Link SWA to the Function App as its backend (`/api/*`)
resource swaLink 'Microsoft.Web/staticSites/linkedBackends@2023-12-01' = {
  parent: swa
  name: 'default'
  properties: {
    backendResourceId: funcApp.id
    region: location
  }
}

// -----------------------------------------------------------------------------
// Outputs
// -----------------------------------------------------------------------------
output cosmosEndpoint string         = cosmos.properties.documentEndpoint
output cosmosDatabaseName string     = cosmosDb.name
output functionAppName string        = funcApp.name
output functionAppHostName string    = funcApp.properties.defaultHostName
output staticWebAppName string       = swa.name
output staticWebAppHostName string   = swa.properties.defaultHostname
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output storageAccountName string     = storage.name
