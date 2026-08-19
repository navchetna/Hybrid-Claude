# Hybrid AI


## Command 
```
claude --agents "$(cat agents/contractor.json)" --append-system-prompt "$(cat system-prompt/prompt.md)" -p "Clone the vLLM and setup the cpu version, run the vllm test for cpu in temp folder within the current directory" --output-format stream-json --dangerously-skip-permissions
```