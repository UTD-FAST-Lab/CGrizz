# Mode 1 — same as before, just add "single"
python pipeline_orchestrator.py single \
    --scg ./data/A/scg.json --dcg ./data/A/dcg.json \
    --boundary-edges ./data/A/edge1.json \
    --target-program ./programs/A --sa-tool ./tools/sa

# Mode 2 — all edges of one program, concurrently
python pipeline_orchestrator.py parallel-edges \
    --config ./pipeline_config.json --program program_A \
    --concurrency 5

# Mode 3 — all programs × all edges, concurrently
python pipeline_orchestrator.py parallel-all \
    --config ./pipeline_config.json \
    --concurrency 5

python3 pipeline_orchestrator.py single --program-name VC4 --target-program ../programs/cats/VC4 --main-class cats.vc4.vc.Class --sa-tool-name WALA --sa-algorithm 0-CFA --sa-tool ../tools/WALA-1.5.7 --boundaries ../input_data/cats/VC4/WALA/0-CFA/boundaries.json --boundary-index 0 --output-dir ../results



results/
└── program_A/
    └── WALA/
        └── 0-CFA/
            ├── b0_Handler.dispatch__ConcreteHandler.handlePost/
            │   ├── unsoundness_report.md
            │   ├── test_case/
            │   ├── annotated_sa_tool/
            │   └── pipeline_result.json
            └── b1_App.run__ServiceImpl.execute/
                └── ...