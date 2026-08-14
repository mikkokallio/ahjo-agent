# Coding Agent Setup Notes

These are copy-ready prompts for a coding agent working in a fresh clone. The agent may use the repository files as the source of truth, but it must not commit credentials, generated data, downloaded source documents, or `.azure` state.

## Local UI With Existing Azure Backends

This path runs the FastAPI UI locally. It still needs an existing Microsoft Foundry hosted agent, PDF processor Function App, Azure AI Search index, and permission to use them.

```text
Set up this Ahjo Meeting Agent repository for local development on my machine and leave the UI running.

Work autonomously, but do not provision, modify, or delete Azure resources. Read README.md, azure.yaml, the requirements files, scripts/download-strategy.ps1, scripts/index_strategy.py, and the existing tests before acting. Use the repository's existing architecture and commands rather than replacing frameworks.

Requirements:
1. Check that Python 3.13, PowerShell 7, Azure CLI, azd, and Git are available. Use an existing authenticated Azure/azd session; ask me to authenticate directly if needed. Never ask me to paste a password, token, client secret, Function key, or connection string into chat.
2. Create or reuse a repository-local .venv and install the UI and PDF processor requirements needed by the UI, tests, strategy downloader/indexer, and Playwright. Install the Playwright Chromium browser if it is absent.
3. Run scripts/download-strategy.ps1. The PDF is intentionally ignored by Git. Do not weaken its HTTPS host or checksum validation.
4. Resolve AGENT_ENDPOINT, PDF_PROCESSOR_URL, PDF_PROCESSOR_KEY, AZURE_AI_ENDPOINT, AZURE_AI_SEARCH_ENDPOINT, and AZURE_AI_EMBEDDING_DEPLOYMENT_NAME from the selected azd environment or existing local environment without printing secret values. Do not write secrets to tracked files.
5. Check whether the strategy-2025-2029 Azure AI Search index already contains data. If it is absent, run scripts/index_strategy.py using DefaultAzureCredential. Ask before replacing an existing populated index.
6. Run the focused unit tests, Ruff, Python compilation, and ui/test_browser_security.py. Fix only repository problems that block local setup and rerun the affected checks.
7. Start the FastAPI UI from ui/ on http://127.0.0.1:8080 with the resolved environment. Confirm that meetings and agendas load and that a real chat response completes. Do not test or open an Azure SCM/Kudu URL.
8. Report the local URL, test results, which Azure backends were reused, and any remaining manual step. Do not commit or push unless I explicitly request it.
```

## Complete Azure Deployment

This path creates a new Azure environment and deploys the Foundry project and models, hosted agent, PDF processor, authenticated App Service UI, search index, storage, and monitoring. It creates billable resources.

```text
Deploy this Ahjo Meeting Agent repository end to end into a new Azure environment and validate the live application.

First read README.md, azure.yaml, all files under infra/, the service requirements, scripts/download-strategy.ps1, scripts/index_strategy.py, and the tests. Follow current Microsoft Foundry and Azure deployment guidance. Present a short resource and cost-impact plan, then obtain my approval before provisioning billable resources. Ask only for unresolved choices such as subscription, region, environment name, and Entra application ownership. Never ask me to paste secrets into chat or print secret values.

Requirements:
1. Check prerequisites: Python 3.13, PowerShell 7, Azure CLI, azd with the required Foundry extension, Git, and a usable Azure subscription. Have me complete interactive az/azd authentication directly when required. Use a fresh azd environment and keep generated `.azure` state untracked.
2. Use infra/main.bicep and azure.yaml as the source of truth. Preserve managed identity and Entra ID authentication wherever supported. Do not replace identity-based Azure access with keys. The Function key used between the UI/agent and PDF processor may be stored only as a secret azd/App Service setting and sent in the x-functions-key header.
3. Provision the Foundry project, gpt-5.4 deployment, Azure AI Search, storage, monitoring, and hosted-agent prerequisites. Verify that the configured embedding deployment exists and is compatible with scripts/index_strategy.py; deploy the configured embed-v-4-0 model if needed. Respect regional model capacity and ask before selecting a materially different model or SKU.
4. Deploy infra/pdf-processor.bicep into the azd resource group with globally unique Function App and storage names. Package function_app.py, requirements.txt, and host.json at the ZIP root, then deploy with remote build. Obtain its HTTPS base URL and Function key without displaying the key. Set PDF_PROCESSOR_URL and PDF_PROCESSOR_KEY in the azd environment.
5. Run scripts/download-strategy.ps1. The official Helsinki PDF is intentionally absent from Git. Keep the host and SHA-256 checks intact. Grant the signed-in deployment identity only the roles required by scripts/index_strategy.py, set its endpoint variables from azd outputs, and populate the strategy-2025-2029 search index.
6. Deploy the ahjo-meetings hosted agent through azd, capture its Responses 2.0 endpoint as AGENT_ENDPOINT, and smoke-test a direct response. Keep secrets out of logs and tracked files.
7. Choose a globally unique UI_APP_NAME. Create or configure a Microsoft Entra app registration for `https://<UI_APP_NAME>.azurewebsites.net/.auth/login/aad/callback`. Store its client ID and secret in the azd environment, set ENABLE_UI=true, and provision/deploy the single-instance Linux App Service UI. Preserve persistent SQLite under /home and do not add Redis.
8. Run all unit tests, Ruff, Python compilation, Bicep validation, and deterministic Playwright security scenarios. Validate the normal application URL only: anonymous access must redirect to Entra, and after I complete sign-in, meetings, attachments, strategy search, PDF processing, and a real chat response must work. Never use an SCM/Kudu page as the application test URL.
9. Check Azure resource health and application logs for material errors, and report deployed resource names, the normal application URL, validation results, estimated recurring cost, and any manual follow-up. Do not commit environment state, credentials, downloaded PDFs, caches, generated outputs, or deployment ZIPs. Do not commit or push unless I explicitly request it.
```