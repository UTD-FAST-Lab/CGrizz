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
    "BOUNDARY_CONTAINER": "boundary_container",
    "JCG_IMAGE": "jcg_image_test_case",
}


def static_callgraph(framework: str, algo: str, project: str, reproducing_test_case_path: str, boundary_id: str):
        """Generate static call graph"""
        build_jcg_image()
        
        staticcg_path = Path(reproducing_test_case_path) / "staticcg"
        os.makedirs(staticcg_path, exist_ok=True)
        (staticcg_path / "log.txt").touch()

        mounts = [
            (str(Path(reproducing_test_case_path) / "target"), "/repo", True),
            (str(Path(reproducing_test_case_path) / "jcg.conf"), f"/jcg-conf/{project}.conf", True),
            (str(staticcg_path), "/staticcg", False),
            (str(staticcg_path / "log.txt"), "/log.txt", False),
        ]
 
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
        docker_run(f'{config["STATICCG_CONTAINER"]}_{boundary_id}_{framework}_{algo}', config["JCG_IMAGE"], mounts, cmd)



def dynamic_callgraph(project: str, reproducing_test_case_path: str, boundary_id: str):
        """Generate dynamic call graph for a specific experiment directory"""
        build_jcg_image()
        
        dyncg_path = Path(reproducing_test_case_path) / "dyncg"
        os.makedirs(dyncg_path, exist_ok=True)
        
        mounts = [
            (str(Path(reproducing_test_case_path) / "target"), "/repo", True),
            (str(Path(reproducing_test_case_path) / "jcg.conf"), f"/jcg-conf/{project}.conf", True),
            (str(dyncg_path), "/dyncg", False),
        ]
        
        cmd = [
            config["STATICCG_TIMEOUT"],
            config["STATICCG_MEMORY"],
            "runMain", "Evaluation",
            "--input", "/jcg-conf/",
            "--output", "/dyncg",
            "--adapter", "Dynamic",
            "--algorithm-prefix", "Dynamic",
            "--project-prefix", project,
            "--debug"
        ]
        
        docker_remove(f'{config["DYNAMICCG_CONTAINER"]}_{boundary_id}_{framework}_{algo}')
        docker_run(f'{config["DYNAMICCG_CONTAINER"]}_{boundary_id}_{framework}_{algo}', config["JCG_IMAGE"], mounts, cmd)


def boundary_generation(framework: str, algo: str, project: str, reproducing_test_case_path: str, boundary_id: str):
    """Generate boundary call graph for a specific experiment directory"""
    
    build_jcg_image()
    
    dyncg_path = Path(reproducing_test_case_path) / "dyncg" / project / "Dynamic" / "Dynamic"
    staticcg_path = Path(reproducing_test_case_path) / "staticcg" / project / framework / algo
    boundary_path = Path(reproducing_test_case_path) / "boundary" / framework / algo

    if os.path.exists(boundary_path / "boundaries.json"):
        return  # Boundary already generated, skip

    if not os.path.exists(dyncg_path / "cg.json"):
        try:
            dynamic_callgraph(project, reproducing_test_case_path)
        except Exception as e:
            print(f"Error occurred while generating dynamic call graph for {project}: {e}")
    if not os.path.exists(staticcg_path / "cg.json"):
        try:
            static_callgraph(framework, algo, project, reproducing_test_case_path)
        except Exception as e:
            print(f"Error occurred while generating static call graph for {project} with {framework} and {algo}: {e}")


    os.makedirs(boundary_path, exist_ok=True)

    mainClass = get_main_class(reproducing_test_case_path)
    
    mounts = [
        (str(dyncg_path), "/dynamiccg", True),
        (str(staticcg_path), "/staticcg", True),
        (str(boundary_path), "/output", False),
    ]
    
    cmd = [
        config["STATICCG_TIMEOUT"],
        config["STATICCG_MEMORY"],
        "runMain", "CompareCGs",
        "--input1", "/staticcg/cg.json",
        "--input2", "/dynamiccg/cg.json",
        "--mainClass", mainClass,
        "--output", "/output",
        "--showPrecisionRecall", "all",
        "--showBoundaries",
        "--nonStrict"
    ]
    
    docker_remove(f'{config["BOUNDARY_CONTAINER"]}_{boundary_id}_{framework}_{algo}')
    try:
        docker_run(f'{config["BOUNDARY_CONTAINER"]}_{boundary_id}_{framework}_{algo}', config["JCG_IMAGE"], mounts, cmd)
    except Exception as e:
        print(f"Error occurred while generating boundary call graph for {project}: {e}")


def project_build(project: str, reproducing_test_case_path: str):
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
    dockerfile = context / "Dockerfile"
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

def get_main_class(reproducing_test_case_path: str):

    with open(Path(reproducing_test_case_path) / "jcg.conf", "r") as f:
        data = json.load(f)
    
    return data['main']

def main():
    parser = argparse.ArgumentParser(description="JCG Runner")
    parser.add_argument("--framework", required=False, help="Framework to analyze ()")
    parser.add_argument("--algorithm", required=False, help="Algorithm to use (e.g., Class Hierarchy Analysis)")
    parser.add_argument("--project", required=True, help="Project name (must match test case directory)")
    parser.add_argument("--type", required=True, choices=["static", "dynamic", "boundary"], help="Type of call graph to generate")

    parser.add_argument("--reproducing_test_case_path", required=True, help="Path to the reproducing test case")
    parser.add_argument("--boundary_id", help="Id of the boundary edge")

    args = parser.parse_args()

    project_build(args.project, args.reproducing_test_case_path)

    if args.type == "static":
        if not args.framework or not args.algorithm:
            print("Error: --framework and --algorithm are required for static call graph generation")
            sys.exit(1)
        static_callgraph(args.framework, args.algorithm, args.project, args.reproducing_test_case_path, args.boundary_id)
    elif args.type == "dynamic":
        dynamic_callgraph(args.project, args.reproducing_test_case_path, args.boundary_id)

    elif args.type == "boundary":
            boundary_generation(args.framework, args.algorithm, args.project, args.reproducing_test_case_path, args.boundary_id)

if __name__ == "__main__":
    main()