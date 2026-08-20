# Hybrid AI

## Instructions
### Setup LiteLLM
1. Go to litellm folder
    ```
    cd litellm
    ```
2. Start the containers
    ```
    docker compose up
    ```
3. Go to `localhost:4000/ui/playground`
4. Login with the credentials and create a virtual key

### Installing Claude Plugin
1. Install the plugin
```claude --plugin-dir .```

2. To run any task

## Command 
```
export ANTHROPIC_FOUNDRY_BASE_URL=https://ei-api.mg2.eglb.intel.com
export ANTHROPIC_FOUNDRY_API_KEY=sk-xZ68PyuImspqA0TFl6flJw
export ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-4-5
export ANTHROPIC_DEFAULT_OPUS_MODEL=claude-sonnet-4-5
export CLAUDE_CODE_USE_FOUNDRY=1
```

## Install Claude Plugin
1. Register the plugin in local marketplace
```
claude plugin marketplace add ./
```
2. Install the plugin
```
claude plugin install local-subagent
```

## Run the plugin routing
1. Interactive Mode
    - Run the claude code

2. Headless mode
    - claude -p "/local-subagent:contract  is there a bug in the codebase?"
