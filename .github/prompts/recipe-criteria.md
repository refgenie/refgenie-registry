## Recipe Review Criteria

1. **SECURITY** (critical - review with extreme care):
   You MUST check each of the following. Report any violation.
   - [ ] No commands that pipe remote content to a shell (curl|bash, wget -O-|sh, python -c "import urllib...")
   - [ ] No hardcoded URLs outside of declared tool sources in the requires section
   - [ ] No environment variable reading or exfiltration ($HOME, $USER, $AWS_SECRET, env, printenv, etc.)
   - [ ] No file operations outside the declared output directory ({output_dir})
   - [ ] All tool sources from approved registries (bioconda, conda-forge, pip with pinned versions)
   - [ ] No sudo or root operations
   - [ ] No background processes or daemons (& , nohup, screen, tmux, systemctl)
   - [ ] No network calls during the build phase (the build.command section should be offline)
   - [ ] No code injection risk via template variables (e.g., unquoted {genome} in shell commands)
   - [ ] No base64 encoding/decoding of commands
   - [ ] No use of eval, exec, or dynamic code execution

2. **QUALITY**: Is the description clear and accurate? Are resource estimates (memory, disk, time) reasonable for the tool and typical genome sizes? Are all output files documented in the outputs section? Do the tests validate correctness beyond just file existence (e.g., checking file is valid, running a quick tool command)?

3. **APPROPRIATENESS**: Is this a commonly used bioinformatics tool? Does the asset type fit refgenie's scope (reference genome derived assets like indexes, annotations, derived sequences)? Is there already a similar recipe in the registry that this would duplicate?
