# Hybrid AI

## Instructions
### Setup LiteLLM
1. Start the litellm configuration
    ```
    docker run \
    -v $(pwd)/config.yaml:/app/config.yaml \
    -e OPENAI_API_KEY=$OPENAI_API_KEY \
    -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    -e LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY \
    -e DATABASE_URL=$DATABASE_URL \
    -p 4000:4000 \
    docker.litellm.ai/berriai/litellm:latest \
    --config /app/config.yaml
    ```
    ```
    litellm --config litellm/config.yaml --port 4000
    ```

## Command 
```
claude --agents "$(cat agents/contractor.json)" --append-system-prompt "$(cat system-prompt/prompt.md)" -p "Clone the vLLM and setup the cpu version, run the vllm test for cpu in temp folder within the current directory" --output-format stream-json --dangerously-skip-permissions
```