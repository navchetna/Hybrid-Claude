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
export ANTHROPIC_FOUNDRY_BASE_URL=http://localhost:4000/
export ANTHROPIC_FOUNDRY_API_KEY=sk-ieOb-E0NaZ8meQ_AZIK9Fw
export ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-4-5
export ANTHROPIC_DEFAULT_OPUS_MODEL=claude-sonnet-4-5
export CLAUDE_CODE_USE_FOUNDRY=1
```
claude --agents "$(cat agents/contractor.json)" --append-system-prompt "$(cat system-prompt/prompt.md)" -p "Clone the vLLM and setup the cpu version, run the vllm test for cpu in temp folder within the current directory" --output-format stream-json --dangerously-skip-permissions --verbose
```