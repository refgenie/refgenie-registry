## New Recipe: [asset name]

**Primary tool:** [tool name and version]
**Tool source:** [bioconda / conda-forge / pip / apt]

### Checklist

- [ ] YAML file is at `recipes/<asset_name>/recipe.yaml`
- [ ] Passes schema validation (`python tools/validate_recipe.py <file>`)
- [ ] `name` matches the directory name
- [ ] `version` follows semver (e.g., 1.0.0)
- [ ] Build commands use only declared variables ({fasta}, {output_dir}, {genome}, {threads})
- [ ] No suspicious commands (curl|bash, wget|sh, hardcoded credentials)
- [ ] Tools are from approved sources (bioconda, conda-forge)
- [ ] All output files are declared in `outputs`
- [ ] Tests validate that outputs are correct

### Security self-check

- [ ] No network access during build (only tool install)
- [ ] No file access outside `{output_dir}`
- [ ] No environment variable exfiltration
- [ ] No sudo/root operations
- [ ] No background processes or daemons

### Notes

<!-- Any additional context for reviewers -->
