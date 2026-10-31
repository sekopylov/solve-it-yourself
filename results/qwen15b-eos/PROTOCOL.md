# Controlled completion-format fix

Fix the Qwen/TRL duplicated end token and exclude tokens after the first response
end from training and perplexity. Use the unchanged `config.yaml`: original
480/60/60 split, Qwen2.5-1.5B-Instruct revision, seed 42, LoRA rank 16, learning
rate 5e-5, beta 0.1, effective batch 16, two epochs (60 steps). No SFT stage.

Evaluate the **final step 60**, chosen before training, on the existing 24-case
dev suite. Compare it with the archived first-run step 60. Do not run a checkpoint
search. If refusal behavior improves, also run the final checkpoint on the existing
84-case comparison suite, reusing the unchanged base/system outputs. These are
repeated diagnostic comparisons, not new untouched tests. Keep the rubric fixed.

Preserve old runs. New histories, PPL and response-format metadata belong in a new
run directory. PPL now stops at the first EOS, so historical PPL values using the
post-EOS newline are not directly interchangeable. The prior audit is recorded in
`audit-before.json`; its EOS attribution is observational, not proof of causation.
