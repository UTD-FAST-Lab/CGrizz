import shutil
from pathlib import Path

BENCHMARK_DIR = Path("/20TB/mohammad/benchmarks")


def program_setup():

    # output_root = Path("programs/cats")
    # output_root.mkdir(parents=True, exist_ok=True)

    # input_dir = BENCHMARK_DIR / "CATS-Microbenchmark" / "benchmarks"

    # # recursively find all jar files
    # for jar_file in input_dir.rglob("*.jar"):

    #     target_dir = jar_file.parent

    #     if target_dir.name != "target":
    #         continue

    #     app_dir = target_dir.parent
    #     src_dir = app_dir / "src"

    #     if not src_dir.exists():
    #         print(f"[WARNING] Missing src for {jar_file}")
    #         continue

    #     app_name = jar_file.stem

    #     dest_app_dir = output_root / app_name
    #     dest_src_dir = dest_app_dir / "src"

    #     dest_app_dir.mkdir(parents=True, exist_ok=True)

    #     # overwrite src if already exists
    #     if dest_src_dir.exists():
    #         shutil.rmtree(dest_src_dir)

    #     shutil.copytree(src_dir, dest_src_dir)

    #     # copy jar
    #     shutil.copy2(jar_file, dest_app_dir / jar_file.name)

    #     print(f"[INFO] Copied {app_name}")


    # # njr
    # output_root = Path("programs/njr")
    # output_root.mkdir(parents=True, exist_ok=True)
    # input_dir = BENCHMARK_DIR / "njr-1-dataset" / "june2020_dataset"

    #     # recursively find all jar files
    # for jar_file in input_dir.rglob("*.jar"):

    #     target_dir = jar_file.parent

    #     if target_dir.name != "jarfile":
    #         continue

    #     app_dir = target_dir.parent
    #     src_dir = app_dir / "src"

    #     if not src_dir.exists():
    #         print(f"[WARNING] Missing src for {jar_file}")
    #         continue

    #     app_name = jar_file.stem

    #     dest_app_dir = output_root / app_name
    #     dest_src_dir = dest_app_dir / "src"

    #     dest_app_dir.mkdir(parents=True, exist_ok=True)

    #     # overwrite src if already exists
    #     if dest_src_dir.exists():
    #         shutil.rmtree(dest_src_dir)

    #     shutil.copytree(src_dir, dest_src_dir)

    #     # copy jar
    #     shutil.copy2(jar_file, dest_app_dir / jar_file.name)

    #     print(f"[INFO] Copied {app_name}")



    # xcorpus
    output_root = Path("../programs/xcorpus")
    output_root.mkdir(parents=True, exist_ok=True)
    input_dir = BENCHMARK_DIR / "xcorpus_recall_benchmark" / "projects"

    for project in input_dir.iterdir():

        if not project.is_dir():
            continue

        app_name = project.name
        dest_app_dir = output_root / app_name
        dest_app_dir.mkdir(parents=True, exist_ok=True)

        temp_dir = project / "temp"
        if not temp_dir.exists():
            print(f"[WARNING] Missing temp directory for {app_name}")
            continue

        shutil.copytree(
            temp_dir,
            dest_app_dir / "temp",
            dirs_exist_ok=True
        )

        print(f"[INFO] Copied {app_name}")



def boundary_setup():

    # # cats
    # output_root = Path("../input_data/cats")
    # output_root.mkdir(parents=True, exist_ok=True)
    # input_dir = BENCHMARK_DIR / "CATS-Microbenchmark" / "programs"

    # for program in input_dir.iterdir():

    #     if not program.is_dir():
    #         continue

    #     app_name = program.name
    #     dest_app_dir = output_root / app_name
    #     dest_app_dir.mkdir(parents=True, exist_ok=True)

    #     # for each tool/algorithm if the boundareis.json exists, copy the tool dir to the dest_app_dir
    #     boundary_dir = program / "boundary"
    #     if boundary_dir.exists():
    #         for tool_dir in boundary_dir.iterdir():
    #             if tool_dir.is_dir():
    #                 shutil.copytree(tool_dir, dest_app_dir / tool_dir.name)
    #                 print(f"[INFO] Copied {app_name} - {tool_dir.name}")
    #     else:
    #         print(f"[WARNING] Missing boundary for {app_name}")

    # njr

    # output_root = Path("../input_data/njr")
    # output_root.mkdir(parents=True, exist_ok=True)
    # input_dir = BENCHMARK_DIR / "njr-1-dataset" / "june2020_dataset"

    # for program in input_dir.iterdir():

    #     if not program.is_dir():
    #         continue

    #     app_name = program.name
    #     dest_app_dir = output_root / app_name
    #     dest_app_dir.mkdir(parents=True, exist_ok=True)

    #     boundary_dir = program / "boundary"
    #     if boundary_dir.exists():
    #         for tool_dir in boundary_dir.iterdir():
    #             if tool_dir.is_dir():
    #                 shutil.copytree(tool_dir, dest_app_dir / tool_dir.name)
    #                 print(f"[INFO] Copied {app_name} - {tool_dir.name}")
    #     else:
    #         print(f"[WARNING] Missing boundary for {app_name}")


    # xcorpus

    output_root = Path("../input_data/xcorpus")
    output_root.mkdir(parents=True, exist_ok=True)
    input_dir = BENCHMARK_DIR / "xcorpus_recall_benchmark" / "projects"

    for program in input_dir.iterdir():

        if not program.is_dir():
            continue

        app_name = program.name
        dest_app_dir = output_root / app_name
        dest_app_dir.mkdir(parents=True, exist_ok=True)

        boundary_dir = program / "boundary"
        if boundary_dir.exists():
            for tool_dir in boundary_dir.iterdir():
                if tool_dir.is_dir():
                    shutil.copytree(tool_dir, dest_app_dir / tool_dir.name)
                    print(f"[INFO] Copied {app_name} - {tool_dir.name}")
        else:
            print(f"[WARNING] Missing boundary for {app_name}")


def main():

    program_setup()
    # boundary_setup()





if __name__ == '__main__':
    main()