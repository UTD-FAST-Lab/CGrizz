import argparse
import os
import subprocess
import sys
import json
import shutil
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime


config = {
    "UID": os.getuid(),
    "GID": os.getgid(),
    "STATICCG_TIMEOUT": "350m",  
    "STATICCG_MEMORY": "400G", 
    "STATICCG_CONTAINER": "staticcg_container",
    "DYNAMICCG_CONTAINER": "dyncg_container",
    "JCG_IMAGE": "jcg_image_annotation",
    "WALA_ORG_PATH": Path(__file__).parent.parent.parent / "tools" / "wala",
    "OPAL_ORG_PATH": Path(__file__).parent.parent.parent / "tools" / "opal",
    "SOOT_ORG_PATH": Path(__file__).parent.parent.parent / "tools" / "soot"
}

SOURCE_SUFFIXES = (".java", ".scala")
SKIP_DIRS = {"target", "build", "out", ".git", ".gradle", ".idea", ".bsp", "node_modules"}


def changed_sources(annotated: Path, original: Path) -> List[str]:
    """
    Tool source files the annotator changed (relative paths), found by comparing
    the annotated copy with the original tool. copytree keeps mtimes, so only
    files whose size or mtime differ are read.
    """
    changed = []
    for root, dirs, files in os.walk(annotated):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(SOURCE_SUFFIXES):
                continue
            a = Path(root) / name
            rel = a.relative_to(annotated)
            o = original / rel
            if not o.exists():
                changed.append(str(rel))
                continue
            sa, so = a.stat(), o.stat()
            if sa.st_size != so.st_size or (sa.st_mtime != so.st_mtime and a.read_bytes() != o.read_bytes()):
                changed.append(str(rel))
    return changed


def container_env(framework: str, annotated_sa_tool_path: str, fast_path: bool) -> Dict[str, str]:
    """
    SA_FRAMEWORK selects the tool rebuilt by TC_entrypoint.sh. With --fast-path,
    CHANGED_SOURCES lets it compile just those files on top of the prebuilt tool
    (WALA, Soot) instead of rebuilding the whole tool and JCG. CGRIZZ_MARKER_SCOPE
    (config.TOOL_MARKER_SCOPE) tells the tool's marker module where to warn.
    """
    env = {"SA_FRAMEWORK": framework.upper()}
    if os.environ.get("CGRIZZ_MARKER_SCOPE"):
        env["CGRIZZ_MARKER_SCOPE"] = os.environ["CGRIZZ_MARKER_SCOPE"]
    if fast_path:
        original = config.get(f"{framework.upper()}_ORG_PATH")
        files = changed_sources(Path(annotated_sa_tool_path), Path(original)) if original else []
        env["JCG_FAST_PATH"] = "1"
        env["CHANGED_SOURCES"] = ":".join(files)
        print(f"Fast path: changed tool sources: {files or 'none'}")
    return env


def static_callgraph(framework: str, algo: str, project: str, reproducing_test_case_path: str, annotated_sa_tool_path: str, boundary_id: str, fast_path: bool = False):
        """Generate static call graph"""
        staticcg_path = Path(reproducing_test_case_path) / "staticcg"
        os.makedirs(staticcg_path, exist_ok=True)
        (staticcg_path / "log.txt").touch()

        project_build(reproducing_test_case_path)

        mounts = [
            (str(Path(reproducing_test_case_path) / "target"), "/repo", True),
            (str(Path(reproducing_test_case_path) / "jcg.conf"), f"/jcg-conf/{project}.conf", True),
            (str(staticcg_path), "/staticcg", False),
            (str(staticcg_path / "log.txt"), "/log.txt", False),
        ]

        # mount the annotated SA tool for the given framework and mount the original tools for other frameworks. all tools are (wala, opal, soot)
        if framework == "WALA":
            mounts.append((annotated_sa_tool_path, "/WALA", False))
            mounts.append((config["OPAL_ORG_PATH"], "/OPAL", False))
            mounts.append((config["SOOT_ORG_PATH"], "/SOOT", False))
        elif framework == "OPAL":
            mounts.append((annotated_sa_tool_path, "/OPAL", False))
            mounts.append((config["WALA_ORG_PATH"], "/WALA", False))
            mounts.append((config["SOOT_ORG_PATH"], "/SOOT", False))
        elif framework.upper() == "SOOT":
            mounts.append((annotated_sa_tool_path, "/SOOT", False))
            mounts.append((config["WALA_ORG_PATH"], "/WALA", False))
            mounts.append((config["OPAL_ORG_PATH"], "/OPAL", False))

        

        cmd = [
            config["STATICCG_TIMEOUT"],
            config["STATICCG_MEMORY"],
            "runMain", "Evaluation",
            "--input", "/jcg-conf/",
            "--output", "/staticcg",
            "--adapter", framework,
            "--algorithm-prefix", algo,
            "--project-prefix", project,
            "--debug"
        ]
        
        docker_remove(f'{config["STATICCG_CONTAINER"]}_{boundary_id}_{framework}_{algo}')
        docker_run(f'{config["STATICCG_CONTAINER"]}_{boundary_id}_{framework}_{algo}', config["JCG_IMAGE"], mounts, cmd,
                   env=container_env(framework, annotated_sa_tool_path, fast_path))



def project_build(reproducing_test_case_path: str):
    """Build a project"""
    build_cmd = ["mvn", "clean", "package", "-DskipTests"]
    run_command(build_cmd, cwd=Path(reproducing_test_case_path))

def docker_remove(container: str):
    """Remove a Docker container"""
    run_command(["docker", "rm", "-f", container], check=False)


def run_command(cmd: List[str], cwd: Optional[Path] = None, 
                capture_output: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command"""
    print(f"Running: {' '.join(str(c) for c in cmd)}")
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture_output,
        text=True,
        check=check
    )

def docker_run(name: str, image: str, mounts: List[tuple], 
                cmd: Optional[List[str]] = None, entrypoint: Optional[str] = None,
                interactive: bool = False, redirect_output: Optional[Path] = None,
                env: Optional[Dict[str, str]] = None, tee_output: bool = False):
    """Run a Docker container"""
    docker_cmd = ["docker", "run"]
    
    if interactive:
        docker_cmd.append("-it")
    
    docker_cmd.extend(["--name", name])

    if env:
        for key, value in env.items():
            docker_cmd.extend(["-e", f"{key}={value}"])
    
    for source, target, readonly in mounts:
        mount_str = f"type=bind,source={source},target={target}"
        if readonly:
            mount_str += ",readonly"
        docker_cmd.extend(["--mount", mount_str])
    
    if entrypoint:
        docker_cmd.extend(["--entrypoint", entrypoint])
    
    docker_cmd.append(image)
    
    if cmd:
        docker_cmd.extend(cmd)
    
    if redirect_output:
        if tee_output:
            # Use tee to show output AND save to file
            tee_cmd = " ".join([str(c) for c in docker_cmd]) + f" 2>&1 | tee {redirect_output}"
            subprocess.run(tee_cmd, shell=True)
        else:
            # Just redirect to file
            with open(redirect_output, "w") as f:
                subprocess.run(docker_cmd, stdout=f, stderr=subprocess.STDOUT)
    else:
        run_command(docker_cmd)

def build_jcg_image():
    """Build JCG Docker image"""
    context = Path(__file__).parent
    dockerfile = context / "TC.Dockerfile"
    build_args = {
        "UID": config["UID"],
        "GID": config["GID"]
    }
    docker_build(config["JCG_IMAGE"], dockerfile, context, build_args)
    

def docker_build(tag: str, dockerfile: Path, context: Path, 
                    build_args: Optional[dict] = None, output: Optional[Path] = None):
        """Build a Docker image"""
        cmd = ["docker", "build", f"--tag={tag}"]
        
        if build_args:
            for key, value in build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])
        
        if output:
            cmd.extend(["--output", str(output)])
        
        cmd.extend(["-f", str(dockerfile), str(context)])
        run_command(cmd)


def main():
    parser = argparse.ArgumentParser(description="JCG Runner")
    parser.add_argument("--framework", required=False, help="Framework to analyze (WALA)")
    parser.add_argument("--algorithm", required=False, help="Algorithm to use (e.g., 0-CFA)")
    parser.add_argument("--project", required=True, help="Project name (must match test case directory)")

    parser.add_argument("--reproducing_test_case_path", required=True, help="Path to the reproducing test case")
    parser.add_argument("--annotated_sa_tool_path", help="Path to the annotated static analysis tool")
    parser.add_argument("--boundary_id", help="Id of the boundary edge")
    parser.add_argument("--skip-image-build", action="store_true",
                        help="The pipeline already built the image for this run (pipeline/analysis.py)")
    parser.add_argument("--fast-path", action="store_true",
                        help="Compile only the changed tool sources on top of the prebuilt tool (WALA, Soot)")

    args = parser.parse_args()

    boundary_id = args.boundary_id
    boundary_id = boundary_id.split("_")[0]

    if not args.skip_image_build:
        build_jcg_image()
    static_callgraph(args.framework, args.algorithm, args.project, args.reproducing_test_case_path,
                     args.annotated_sa_tool_path, boundary_id, fast_path=args.fast_path)
    

if __name__ == "__main__":
    main()