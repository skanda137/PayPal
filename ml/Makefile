.PHONY: all test data eval hard demo heldout
all: data eval hard heldout demo
heldout: ; python3 -c "from payoutguard_ml import heldout; heldout.run('data/eval','artifacts/eval/heldout_alias_kind.json'); heldout.run('data/hard','artifacts/hard/eval/heldout_alias_kind.json')"
data:  ; python3 -m payoutguard_ml.cli generate --data data/eval --n-entities 6000
eval:  ; python3 -m payoutguard_ml.cli evaluate --data data/eval --artifacts artifacts
hard:  ; python3 -m payoutguard_ml.cli generate --hard --data data/hard --n-entities 6000 && python3 -c "from payoutguard_ml import evaluate; evaluate.run('data/hard','artifacts/hard')"
demo:  ; python3 -m payoutguard_ml.cli demo --artifacts artifacts
test:  ; python3 -m pytest -q tests
