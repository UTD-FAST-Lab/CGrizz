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


def static_callgraph(framework: str, algo: str, project: str, reproducing_test_case_path: str, annotated_sa_tool_path: str):
        """Generate static call graph"""
        build_jcg_image()
        
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
        elif framework == "SOOT":
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
        
        docker_remove(config["STATICCG_CONTAINER"])
        docker_run(config["STATICCG_CONTAINER"], config["JCG_IMAGE"], mounts, cmd)



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
    
    args = parser.parse_args()

   
    static_callgraph(args.framework, args.algorithm, args.project, args.reproducing_test_case_path, args.annotated_sa_tool_path)
    

if __name__ == "__main__":
    main()