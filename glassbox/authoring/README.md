# Natural-Language Policy Authoring (Legacy)

`glassbox.authoring` translates natural-language descriptions into YAML rules
for the retained v1 `RulesLoader` and `GovernancePipeline` policy model. It does
not create current signed policy bundles or action catalogue definitions.

`nl_policy.py` optionally uses the Anthropic SDK (`pip install -e .[authoring]`)
and otherwise provides a template-based fallback. Model-generated rules are
untrusted proposals: require human review, schema validation, policy tests,
approval, versioning, and controlled activation before use.

Never describe generated output as production-ready solely because it parses.
Prompt input and generated policy text may contain sensitive organizational data;
apply provider, retention, and data-residency controls.

For current runtime extensions, see the
[extension guide](../../docs/DEVELOPMENT/implementation_guide.md).