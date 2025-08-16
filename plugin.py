# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import os
import urllib.request
import urllib.error
import subprocess
import threading
import requests
import mimetypes
import tempfile
from ctypes import byref, windll, wintypes
from PIL import Image
import base64
import websocket
import uuid
from datetime import datetime
from ctypes import wintypes
import urllib.parse

# Data Types
type Response = dict[str, any]

LOG_FILE = os.path.join(os.environ.get("USERPROFILE", "."), "flux_plugin.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Global configuration variables
CONFIG_FILE = os.path.join(
    f'{os.environ.get("PROGRAMDATA", ".")}{r'\NVIDIA Corporation\nvtopps\rise\plugins\flux'}',
    "config.json",
)
GALLERY_DIRECTORY = None
NVIDIA_API_KEY = None
NGC_API_KEY = None
HF_TOKEN = None
LOCAL_NIM_CACHE = None
OUTPUT_DIRECTORY = os.path.join(os.environ.get("USERPROFILE", "."), "flux_output")
BUILD_NVIDIA_COM_FLUX_HOSTED_NIM = (
    "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev"
)
FLUX_NIM_URL = None
INVOKEAI_URL = "http://localhost:9090"
FLUX_KONTEXT_NIM_URL = "http://localhost:8011"
COMFYUI_URL = "http://localhost:8188"
FLUX_KONTEXT_INFERENCE_BACKEND = "NIM"  # Default to NIM backend
BOARD_ID = None


def set_desktop_background(image_path: str) -> bool:
    """
    Sets the specified image as the desktop background.

    Args:
        image_path (str): Full path to the image file

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Check if the image file exists
        if not os.path.exists(image_path):
            logging.error(f"Image file does not exist: {image_path}")
            return False

        # Use Windows API to set the desktop background
        SPI_SETDESKWALLPAPER = 0x0014
        SPIF_UPDATEINIFILE = 0x01
        SPIF_SENDCHANGE = 0x02

        # Convert the path to absolute path
        abs_path = os.path.abspath(image_path)

        # Set the desktop background
        result = windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, abs_path, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
        )

        if result:
            logging.info(f"Successfully set desktop background to: {abs_path}")
            return True
        else:
            logging.error(f"Failed to set desktop background to: {abs_path}")
            return False

    except Exception as e:
        logging.error(f"Error setting desktop background: {e}")
        return False


def prepare_image_for_kontext(
    image_path: str, target_width: int = 1392, target_height: int = 752
) -> str:
    """
    Scales and crops an image to the specified dimensions and returns it as a base64 encoded string.

    Args:
        image_path (str): Path to the input image file
        target_width (int): Target width (default: 1392)
        target_height (int): Target height (default: 752)

    Returns:
        str: Base64 encoded image data with data URI prefix, or None if failed
    """
    try:
        # Open the image
        with Image.open(image_path) as img:
            # Convert to RGB if necessary
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Calculate scaling to maintain aspect ratio
            img_width, img_height = img.size
            scale = max(target_width / img_width, target_height / img_height)

            # Scale the image
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            scaled_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Crop to target dimensions (center crop)
            left = (new_width - target_width) // 2
            left = max(0, left)  # Ensure left is not negative
            top = (new_height - target_height) // 2
            top = max(0, top)  # Ensure top is not negative
            right = min(new_width, left + target_width)
            bottom = min(new_height, top + target_height)

            cropped_img = scaled_img.crop((left, top, right, bottom))

            # Convert to base64
            import io

            buffer = io.BytesIO()
            cropped_img.save(buffer, format="PNG")
            img_data = buffer.getvalue()

            # Encode to base64 and add data URI prefix
            base64_data = base64.b64encode(img_data).decode("utf-8")
            data_uri = f"data:image/png;base64,{base64_data}"

            logging.info(
                f"Successfully prepared image {image_path} to {target_width}x{target_height}"
            )
            return data_uri

    except Exception as e:
        logging.error(f"Error preparing image {image_path}: {e}")
        return None


def prepare_image_for_comfyui(
    image_path: str, target_width: int = 1392, target_height: int = 752
) -> str:
    """
    Scales and crops an image to the specified dimensions and saves it to a temporary file.
    Returns the path to the temporary file for use with ComfyUI.

    Args:
        image_path (str): Path to the input image file
        target_width (int): Target width (default: 1392)
        target_height (int): Target height (default: 752)

    Returns:
        str: Path to the temporary resized image file, or None if failed
    """
    try:
        # Open the image
        with Image.open(image_path) as img:
            # Convert to RGB if necessary
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Calculate scaling to maintain aspect ratio
            img_width, img_height = img.size
            scale = max(target_width / img_width, target_height / img_height)

            # Scale the image
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            scaled_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Crop to target dimensions (center crop)
            left = (new_width - target_width) // 2
            left = max(0, left)  # Ensure left is not negative
            top = (new_height - target_height) // 2
            top = max(0, top)  # Ensure top is not negative
            right = min(new_width, left + target_width)
            bottom = min(new_height, top + target_height)

            cropped_img = scaled_img.crop((left, top, right, bottom))

            # Create a temporary file
            temp_file = tempfile.NamedTemporaryFile(
                suffix=".png", prefix="comfyui_resized_", delete=False
            )
            temp_path = temp_file.name
            temp_file.close()

            # Save the resized image to the temporary file
            cropped_img.save(temp_path, format="PNG")

            logging.info(
                f"Successfully prepared image {image_path} to {target_width}x{target_height} and saved to {temp_path}"
            )
            return temp_path

    except Exception as e:
        logging.error(f"Error preparing image {image_path} for ComfyUI: {e}")
        return None


def load_config():
    """Load configuration from config.json file"""
    global GALLERY_DIRECTORY, NVIDIA_API_KEY, NGC_API_KEY, HF_TOKEN, LOCAL_NIM_CACHE, OUTPUT_DIRECTORY, FLUX_NIM_URL, INVOKEAI_URL, FLUX_KONTEXT_NIM_URL, COMFYUI_URL, FLUX_KONTEXT_INFERENCE_BACKEND, BOARD_ID
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            GALLERY_DIRECTORY = config.get("GALLERY_DIRECTORY", None)
            NVIDIA_API_KEY = config.get("NVIDIA_API_KEY", None)
            NGC_API_KEY = config.get("NGC_API_KEY", None)
            HF_TOKEN = config.get("HF_TOKEN", None)
            LOCAL_NIM_CACHE = config.get("LOCAL_NIM_CACHE", None)
            OUTPUT_DIRECTORY = config.get("OUTPUT_DIRECTORY", OUTPUT_DIRECTORY)
            FLUX_NIM_URL = config.get("FLUX_NIM_URL", BUILD_NVIDIA_COM_FLUX_HOSTED_NIM)
            INVOKEAI_URL = config.get("INVOKEAI_URL", "http://localhost:9090")
            FLUX_KONTEXT_NIM_URL = config.get(
                "FLUX_KONTEXT_NIM_URL", "http://localhost:8011"
            )
            COMFYUI_URL = config.get("COMFYUI_URL", "http://localhost:8188")
            FLUX_KONTEXT_INFERENCE_BACKEND = config.get(
                "FLUX_KONTEXT_INFERENCE_BACKEND", "NIM"
            )
            BOARD_ID = config.get("BOARD_ID", None)
            logging.info("Configuration loaded successfully")
    except FileNotFoundError:
        logging.warning(f"Config file not found: {CONFIG_FILE}")
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing config file: {e}")
    except Exception as e:
        logging.error(f"Error loading config: {e}")


def main():
    """Main entry point.

    Sits in a loop listening to a pipe, waiting for commands to be issued. After
    receiving the command, it is processed and the result returned. The loop
    continues until the "shutdown" command is issued.

    Returns:
        0 if no errors occurred during execution; non-zero if an error occurred
    """
    # Load configuration on startup
    load_config()

    TOOL_CALLS_PROPERTY = "tool_calls"
    CONTEXT_PROPERTY = "messages"
    SYSTEM_INFO_PROPERTY = "system_info"  # Added for game information
    FUNCTION_PROPERTY = "func"
    INITIALIZE_COMMAND = "initialize"
    SHUTDOWN_COMMAND = "shutdown"

    ERROR_MESSAGE = "Plugin Error!"

    # Generate command handler mapping
    commands = {
        "initialize": execute_initialize_command,
        "shutdown": execute_shutdown_command,
        "flux_nim_ready_check": flux_nim_ready_check,
        "check_nim_status": check_nim_status,
        "stop_nim": stop_nim,
        "start_nim": start_nim,
        "generate_image": generate_image,
        "generate_image_using_kontext": generate_image_using_kontext,
        "invokeai_status": invokeai_status,
        "pause_invokeai_processor": pause_invokeai_processor,
        "resume_invokeai_processor": resume_invokeai_processor,
        "invokeai_empty_model_cache": invokeai_empty_model_cache,
        "flux_kontext_nim_ready_check": flux_kontext_nim_ready_check,
        "check_flux_kontext_nim_status": check_flux_kontext_nim_status,
        "stop_flux_kontext_nim": stop_flux_kontext_nim,
        "start_flux_kontext_nim": start_flux_kontext_nim,
        "comfyui_status": comfyui_status,
        "comfyui_free_memory": comfyui_free_memory,
    }
    cmd = ""

    logging.info("Plugin started")
    while cmd != SHUTDOWN_COMMAND:
        response = None
        input = read_command()
        if input is None:
            logging.error("Error reading command")
            continue

        logging.info(f"Received input: {input}")

        if TOOL_CALLS_PROPERTY in input:
            tool_calls = input[TOOL_CALLS_PROPERTY]
            for tool_call in tool_calls:
                if FUNCTION_PROPERTY in tool_call:
                    cmd = tool_call[FUNCTION_PROPERTY]
                    logging.info(f"Processing command: {cmd}")
                    if cmd in commands:
                        if cmd == INITIALIZE_COMMAND or cmd == SHUTDOWN_COMMAND:
                            response = commands[cmd]()
                        else:
                            response = execute_initialize_command()
                            response = commands[cmd](
                                tool_call.get("params", None),
                                (
                                    input[CONTEXT_PROPERTY]
                                    if CONTEXT_PROPERTY in input
                                    else None
                                ),
                                (
                                    input[SYSTEM_INFO_PROPERTY]
                                    if SYSTEM_INFO_PROPERTY in input
                                    else None
                                ),  # Pass system_info directly
                            )
                    else:
                        logging.warning(f"Unknown command: {cmd}")
                        response = generate_failure_response(
                            f"{ERROR_MESSAGE} Unknown command: {cmd}"
                        )
                else:
                    logging.warning("Malformed input: missing function property")
                    response = generate_failure_response(
                        f"{ERROR_MESSAGE} Malformed input."
                    )
        else:
            logging.warning("Malformed input: missing tool_calls property")
            response = generate_failure_response(f"{ERROR_MESSAGE} Malformed input.")

        logging.info(f"Sending response: {response}")
        write_response(response)

        if cmd == SHUTDOWN_COMMAND:
            logging.info("Shutdown command received, terminating plugin")
            break

    logging.info("G-Assist Plugin stopped.")
    return 0


def read_command() -> dict | None:
    """Reads a command from the communication pipe.

    Returns:
        Command details if the input was proper JSON; `None` otherwise
    """
    try:
        STD_INPUT_HANDLE = -10
        pipe = windll.kernel32.GetStdHandle(STD_INPUT_HANDLE)
        chunks = []

        while True:
            BUFFER_SIZE = 4096
            message_bytes = wintypes.DWORD()
            buffer = bytes(BUFFER_SIZE)
            success = windll.kernel32.ReadFile(
                pipe, buffer, BUFFER_SIZE, byref(message_bytes), None
            )

            if not success:
                logging.error("Error reading from command pipe")
                return None

            # Add the chunk we read
            chunk = buffer.decode("utf-8")[: message_bytes.value]
            chunks.append(chunk)

            # If we read less than the buffer size, we're done
            if message_bytes.value < BUFFER_SIZE:
                break

        retval = buffer.decode("utf-8")[: message_bytes.value]
        return json.loads(retval)

    except json.JSONDecodeError:
        logging.error("Failed to decode JSON input")
        return None
    except Exception as e:
        logging.error(f"Unexpected error in read_command: {str(e)}")
        return None


def write_response(response: Response) -> None:
    """Writes a response to the communication pipe.

    Args:
        response: Function response
    """
    try:
        STD_OUTPUT_HANDLE = -11
        pipe = windll.kernel32.GetStdHandle(STD_OUTPUT_HANDLE)

        json_message = json.dumps(response) + "<<END>>"
        message_bytes = json_message.encode("utf-8")
        message_len = len(message_bytes)

        bytes_written = wintypes.DWORD()
        windll.kernel32.WriteFile(pipe, message_bytes, message_len, bytes_written, None)

    except Exception as e:
        logging.error(f"Failed to write response: {str(e)}")
        pass


def generate_failure_response(message: str = None) -> Response:
    """Generates a response indicating failure.

    Parameters:
        message: String to be returned in the response (optional)

    Returns:
        A failure response with the attached message
    """
    response = {"success": False}
    if message:
        response["message"] = message
    return response


def generate_success_response(message: str = None) -> Response:
    """Generates a response indicating success.

    Parameters:
        message: String to be returned in the response (optional)

    Returns:
        A success response with the attached massage
    """
    response = {"success": True}
    if message:
        response["message"] = message
    return response


def generate_progress_response(
    message: str = None, status: str = "processing"
) -> Response:
    """Generates a progress response for partial updates.

    Parameters:
        message: Progress message to display
        status: Status indicator (processing, success, error)

    Returns:
        A progress response with the attached message and status
    """
    response = {"success": True, "message": message, "status": status}
    return response


def validate_output_directory():
    """Validate that OUTPUT_DIRECTORY can be created and is writable"""
    global OUTPUT_DIRECTORY
    try:
        # Try to create the directory if it doesn't exist
        os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)

        # Test if we can write to the directory
        test_file = os.path.join(OUTPUT_DIRECTORY, ".test_write_permission")
        try:
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)  # Clean up test file
            logging.info(f"OUTPUT_DIRECTORY '{OUTPUT_DIRECTORY}' is valid and writable")
            return True
        except (OSError, PermissionError) as e:
            logging.error(f"OUTPUT_DIRECTORY '{OUTPUT_DIRECTORY}' is not writable: {e}")
            return False

    except (OSError, PermissionError) as e:
        logging.error(f"Failed to create OUTPUT_DIRECTORY '{OUTPUT_DIRECTORY}': {e}")
        return False


def execute_initialize_command() -> dict:
    """Command handler for `initialize` function

    This handler is responseible for initializing the plugin.

    Args:
        params: Function parameters

    Returns:
        The function return value(s)
    """
    logging.info("Initializing plugin")

    # Validate configuration
    validation_results = []

    # Validate OUTPUT_DIRECTORY
    if not validate_output_directory():
        validation_results.append("OUTPUT_DIRECTORY configuration is invalid")

    # Check other critical configurations
    if not GALLERY_DIRECTORY:
        validation_results.append("GALLERY_DIRECTORY not configured")

    if validation_results:
        warning_msg = (
            f"Plugin initialized with warnings: {'; '.join(validation_results)}"
        )
        logging.warning(warning_msg)
        return generate_success_response(f"initialize success. {warning_msg}")
    else:
        logging.info("Plugin initialized successfully with all configurations valid")
        return generate_success_response("initialize success.")


def execute_shutdown_command() -> dict:
    """Command handler for `shutdown` function

    This handler is responsible for releasing any resources the plugin may have
    acquired during its operation (memory, access to hardware, etc.).

    Args:
        params: Function parameters

    Returns:
        The function return value(s)
    """
    logging.info("Shutting down plugin")
    # shutdown function body
    return generate_success_response("shutdown success.")


def flux_nim_ready_check(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `flux_nim_ready_check` function

    Tests health endpoints using the configured FLUX_NIM_URL.

    Args:
        params: Function parameters
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing flux_nim_ready_check with params: {params}")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        # Get the base URL from configuration
        global FLUX_NIM_URL
        if not FLUX_NIM_URL:
            return generate_failure_response(
                "FLUX_NIM_URL not configured. Please set FLUX_NIM_URL in config.json"
            )

        # Check if using NVIDIA hosted service
        if FLUX_NIM_URL.startswith("https://ai.api.nvidia.com"):
            logging.info("Using NVIDIA hosted Flux service - no health check needed")
            return generate_success_response("Using NVIDIA hosted Flux service")

        # Extract base URL for health endpoints (remove /v1/infer if present for local servers)
        base_url = FLUX_NIM_URL

        # Step 1: Test live endpoint
        logging.info("Testing /v1/health/live endpoint...")
        live_url = f"{base_url}/v1/health/live"

        try:
            with urllib.request.urlopen(live_url, timeout=5) as response:
                live_status = response.getcode()
                logging.info(f"Live endpoint status: {live_status}")
                if live_status != 200:
                    return generate_failure_response(
                        f"Live endpoint returned status {live_status}"
                    )
        except urllib.error.URLError as e:
            logging.error(f"Error accessing live endpoint: {e}")
            return generate_failure_response(f"Live endpoint error: {e}")
        except Exception as e:
            logging.error(f"Unexpected error with live endpoint: {e}")
            return generate_failure_response(f"Live endpoint error: {e}")

        # Step 2: Test ready endpoint
        logging.info("Testing /v1/health/ready endpoint...")
        ready_url = f"{base_url}/v1/health/ready"

        try:
            with urllib.request.urlopen(ready_url, timeout=5) as response:
                ready_status = response.getcode()
                logging.info(f"Ready endpoint status: {ready_status}")
                if ready_status != 200:
                    return generate_failure_response(
                        f"Ready endpoint returned status {ready_status}"
                    )
        except urllib.error.URLError as e:
            logging.error(f"Error accessing ready endpoint: {e}")
            return generate_failure_response(f"Ready endpoint error: {e}")
        except Exception as e:
            logging.error(f"Unexpected error with ready endpoint: {e}")
            return generate_failure_response(f"Ready endpoint error: {e}")

        # Step 3: Success response
        logging.info("Both health endpoints are working!")
        final_response = generate_success_response("Service is live and ready!")
        logging.info(f"Final response: {final_response}")
        return final_response

    except Exception as e:
        logging.error(f"Error in flux_nim_ready_check: {str(e)}")
        return generate_failure_response(f"Error in flux_nim_ready_check: {str(e)}")


def check_nim_status(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `check_nim_status` function

    Checks the status of the flux NIM server using WSL and podman.

    Args:
        params: Function parameters
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing check_nim_status with params: {params}")

    try:
        # Check if nim-server container is running using WSL and podman
        logging.info("Checking if nim-server container is running...")
        check_cmd = [
            "wsl",
            "-d",
            "NVIDIA-Workbench",
            "podman",
            "ps",
            "--filter",
            "name=nim-server",
            "--format",
            "{{.Names}}",
        ]

        try:
            result = subprocess.run(
                check_cmd, check=True, capture_output=True, text=True
            )
            container_names = result.stdout.strip()
            logging.info(f"Nim-server container names: {container_names}")

            if container_names:
                return generate_success_response(
                    f"NIM server is running. Container: {container_names}"
                )
            else:
                return generate_failure_response("NIM server is not running.")

        except subprocess.CalledProcessError as e:
            logging.error(f"Error checking NIM server status: {e}")
            return generate_failure_response(f"Error checking NIM server status: {e}")
        except FileNotFoundError:
            logging.error("WSL or podman command not found")
            return generate_failure_response("WSL or podman command not found")
        except Exception as e:
            logging.error(f"Unexpected error checking NIM server status: {e}")
            return generate_failure_response(f"Error checking NIM server status: {e}")

    except Exception as e:
        logging.error(f"Error in check_nim_status: {str(e)}")
        return generate_failure_response(f"Error in check_nim_status: {str(e)}")


def stop_nim(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `stop_nim` function

    Stops the flux NIM server using WSL and podman.

    Args:
        params: Function parameters
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing stop_nim with params: {params}")

    try:
        # Stop the nim-server container using WSL and podman
        logging.info("Stopping nim-server container...")
        stop_cmd = ["wsl", "-d", "NVIDIA-Workbench", "podman", "kill", "nim-server"]

        try:
            result = subprocess.run(
                stop_cmd, check=True, capture_output=True, text=True
            )
            logging.info(f"Nim-server stop result: {result.stdout.strip()}")

            return generate_success_response("NIM server stopped successfully.")

        except subprocess.CalledProcessError as e:
            logging.error(f"Error stopping NIM server: {e}")
            return generate_failure_response(f"Error stopping NIM server: {e}")
        except FileNotFoundError:
            logging.error("WSL or podman command not found")
            return generate_failure_response("WSL or podman command not found")
        except Exception as e:
            logging.error(f"Unexpected error stopping NIM server: {e}")
            return generate_failure_response(f"Error stopping NIM server: {e}")

    except Exception as e:
        logging.error(f"Error in stop_nim: {str(e)}")
        return generate_failure_response(f"Error in stop_nim: {str(e)}")


def start_nim(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `start_nim` function

    Starts the flux NIM server using WSL and podman with configuration from config.json.

    Args:
        params: Function parameters
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing start_nim with params: {params}")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        # Check configuration requirements
        global NGC_API_KEY, HF_TOKEN, LOCAL_NIM_CACHE
        if not NGC_API_KEY or NGC_API_KEY == "YOUR_NGC_API_KEY_HERE":
            return generate_failure_response(
                "NGC API key not configured. Please set NGC_API_KEY in config.json"
            )

        if not HF_TOKEN or HF_TOKEN == "YOUR_HF_TOKEN_HERE":
            return generate_failure_response(
                "HF Token not configured. Please set HF_TOKEN in config.json"
            )

        if not LOCAL_NIM_CACHE or LOCAL_NIM_CACHE == "/path/to/your/nim/cache":
            return generate_failure_response(
                "Local NIM cache path not configured. Please set LOCAL_NIM_CACHE in config.json"
            )

        # Check if NIM server is already running
        logging.info("Checking if Flux NIM server is already running...")
        check_result = check_nim_status()
        if check_result.get("success", False):
            return generate_failure_response("Flux NIM server is already running.")

        # Get port from FLUX_NIM_URL
        port = FLUX_NIM_URL.split(":")[-1]

        # Build the podman command
        logging.info("Starting Flux NIM server...")
        podman_cmd = [
            "wsl",
            "-d",
            "NVIDIA-Workbench",
            "podman",
            "run",
            "-d",
            "--rm",
            "--name=nim-server",
            "--device",
            "nvidia.com/gpu=all",
            "-e",
            f"NGC_API_KEY={NGC_API_KEY}",
            "-e",
            f"HF_TOKEN={HF_TOKEN}",
            "-p",
            f"{port}:8000",
            "-v",
            f"{LOCAL_NIM_CACHE}:/opt/nim/.cache/",
            "nvcr.io/nim/black-forest-labs/flux.1-dev:1.0.0",
        ]

        try:
            # Start the container in the background
            result = subprocess.run(
                podman_cmd, check=True, capture_output=True, text=True
            )
            logging.info(f"NIM server start result: {result.stdout.strip()}")

            return generate_success_response("NIM server started successfully.")

        except subprocess.CalledProcessError as e:
            logging.error(f"Error starting NIM server: {e}")
            return generate_failure_response(f"Error starting NIM server: {e}")
        except FileNotFoundError:
            logging.error("WSL or podman command not found")
            return generate_failure_response("WSL or podman command not found")
        except Exception as e:
            logging.error(f"Unexpected error starting NIM server: {e}")
            return generate_failure_response(f"Error starting NIM server: {e}")

    except Exception as e:
        logging.error(f"Error in start_nim: {str(e)}")
        return generate_failure_response(f"Error in start_nim: {str(e)}")


def flux_kontext_nim_ready_check(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `flux_kontext_nim_ready_check` function

    Tests health endpoints using the configured FLUX_KONTEXT_NIM_URL.

    Args:
        params: Function parameters
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing flux_kontext_nim_ready_check with params: {params}")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        # Get the base URL from configuration
        global FLUX_KONTEXT_NIM_URL
        if not FLUX_KONTEXT_NIM_URL:
            return generate_failure_response(
                "FLUX_KONTEXT_NIM_URL not configured. Please set FLUX_KONTEXT_NIM_URL in config.json"
            )

        # Extract base URL for health endpoints
        base_url = FLUX_KONTEXT_NIM_URL

        # Step 1: Test live endpoint
        logging.info("Testing /v1/health/live endpoint...")
        live_url = f"{base_url}/v1/health/live"

        try:
            with urllib.request.urlopen(live_url, timeout=5) as response:
                live_status = response.getcode()
                logging.info(f"Live endpoint status: {live_status}")
                if live_status != 200:
                    return generate_failure_response(
                        f"Live endpoint returned status {live_status}"
                    )
        except urllib.error.URLError as e:
            logging.error(f"Error accessing live endpoint: {e}")
            return generate_failure_response(f"Live endpoint error: {e}")
        except Exception as e:
            logging.error(f"Unexpected error with live endpoint: {e}")
            return generate_failure_response(f"Live endpoint error: {e}")

        # Step 2: Test ready endpoint
        logging.info("Testing /v1/health/ready endpoint...")
        ready_url = f"{base_url}/v1/health/ready"

        try:
            with urllib.request.urlopen(ready_url, timeout=5) as response:
                ready_status = response.getcode()
                logging.info(f"Ready endpoint status: {ready_status}")
                if ready_status != 200:
                    return generate_failure_response(
                        f"Ready endpoint returned status {ready_status}"
                    )
        except urllib.error.URLError as e:
            logging.error(f"Error accessing ready endpoint: {e}")
            return generate_failure_response(f"Ready endpoint error: {e}")
        except Exception as e:
            logging.error(f"Unexpected error with ready endpoint: {e}")
            return generate_failure_response(f"Ready endpoint error: {e}")

        # Step 3: Success response
        logging.info("Both health endpoints are working!")
        final_response = generate_success_response(
            "Flux Kontext NIM service is live and ready!"
        )
        logging.info(f"Final response: {final_response}")
        return final_response

    except Exception as e:
        logging.error(f"Error in flux_kontext_nim_ready_check: {str(e)}")
        return generate_failure_response(
            f"Error in flux_kontext_nim_ready_check: {str(e)}"
        )


def check_flux_kontext_nim_status(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `check_flux_kontext_nim_status` function

    Checks the status of the flux kontext NIM server using WSL and podman.

    Args:
        params: Function parameters
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing check_flux_kontext_nim_status with params: {params}")

    try:
        # Check if FLUX_KONTEXT container is running using WSL and podman
        logging.info("Checking if FLUX_KONTEXT container is running...")
        check_cmd = [
            "wsl",
            "-d",
            "NVIDIA-Workbench",
            "podman",
            "ps",
            "--filter",
            "name=FLUX_KONTEXT",
            "--format",
            "{{.Names}}",
        ]

        try:
            result = subprocess.run(
                check_cmd, check=True, capture_output=True, text=True
            )
            container_names = result.stdout.strip()
            logging.info(f"Flux Kontext NIM server container names: {container_names}")

            if container_names:
                return generate_success_response(
                    f"Flux Kontext NIM server is running. Container: {container_names}"
                )
            else:
                return generate_failure_response(
                    "Flux Kontext NIM server is not running."
                )

        except subprocess.CalledProcessError as e:
            logging.error(f"Error checking Flux Kontext NIM server status: {e}")
            return generate_failure_response(
                f"Error checking Flux Kontext NIM server status: {e}"
            )
        except FileNotFoundError:
            logging.error("WSL or podman command not found")
            return generate_failure_response("WSL or podman command not found")
        except Exception as e:
            logging.error(
                f"Unexpected error checking Flux Kontext NIM server status: {e}"
            )
            return generate_failure_response(
                f"Error checking Flux Kontext NIM server status: {e}"
            )

    except Exception as e:
        logging.error(f"Error in check_flux_kontext_nim_status: {str(e)}")
        return generate_failure_response(
            f"Error in check_flux_kontext_nim_status: {str(e)}"
        )


def stop_flux_kontext_nim(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `stop_flux_kontext_nim` function

    Stops the flux kontext NIM server using WSL and podman.

    Args:
        params: Function parameters
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing stop_flux_kontext_nim with params: {params}")

    try:
        # Stop the FLUX_KONTEXT container using WSL and podman
        logging.info("Stopping FLUX_KONTEXT container...")
        stop_cmd = [
            "wsl",
            "-d",
            "NVIDIA-Workbench",
            "podman",
            "kill",
            "FLUX_KONTEXT",
        ]

        try:
            result = subprocess.run(
                stop_cmd, check=True, capture_output=True, text=True
            )
            logging.info(
                f"Flux Kontext NIM server stop result: {result.stdout.strip()}"
            )

            return generate_success_response(
                "Flux Kontext NIM server stopped successfully."
            )

        except subprocess.CalledProcessError as e:
            logging.error(f"Error stopping Flux Kontext NIM server: {e}")
            return generate_failure_response(
                f"Error stopping Flux Kontext NIM server: {e}"
            )
        except FileNotFoundError:
            logging.error("WSL or podman command not found")
            return generate_failure_response("WSL or podman command not found")
        except Exception as e:
            logging.error(f"Unexpected error stopping Flux Kontext NIM server: {e}")
            return generate_failure_response(
                f"Error stopping Flux Kontext NIM server: {e}"
            )

    except Exception as e:
        logging.error(f"Error in stop_flux_kontext_nim: {str(e)}")
        return generate_failure_response(f"Error in stop_flux_kontext_nim: {str(e)}")


def start_flux_kontext_nim(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `start_flux_kontext_nim` function

    Starts the flux kontext NIM server using WSL and podman with configuration from config.json.

    Args:
        params: Function parameters
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing start_flux_kontext_nim with params: {params}")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        # Check configuration requirements
        global NGC_API_KEY, HF_TOKEN, LOCAL_NIM_CACHE, FLUX_KONTEXT_NIM_URL
        if not NGC_API_KEY or NGC_API_KEY == "YOUR_NGC_API_KEY_HERE":
            return generate_failure_response(
                "NGC API key not configured. Please set NGC_API_KEY in config.json"
            )

        if not HF_TOKEN or HF_TOKEN == "YOUR_HF_TOKEN_HERE":
            return generate_failure_response(
                "HF Token not configured. Please set HF_TOKEN in config.json"
            )

        if not LOCAL_NIM_CACHE or LOCAL_NIM_CACHE == "/path/to/your/nim/cache":
            return generate_failure_response(
                "Local NIM cache path not configured. Please set LOCAL_NIM_CACHE in config.json"
            )

        # Check if Flux Kontext NIM server is already running
        logging.info("Checking if Flux Kontext NIM server is already running...")
        check_result = check_flux_kontext_nim_status()
        if check_result.get("success", False):
            return generate_failure_response(
                "Flux Kontext NIM server is already running."
            )

        # Get port from FLUX_KONTEXT_NIM_URL
        port = FLUX_KONTEXT_NIM_URL.split(":")[-1]

        # Build the podman command
        logging.info("Starting Flux Kontext NIM server...")
        podman_cmd = [
            "wsl",
            "-d",
            "NVIDIA-Workbench",
            "podman",
            "run",
            "-d",
            "--rm",
            "--name=FLUX_KONTEXT",
            "--device",
            "nvidia.com/gpu=all",
            "-e",
            f"NGC_API_KEY={NGC_API_KEY}",
            "-e",
            f"HF_TOKEN={HF_TOKEN}",
            "-p",
            f"{port}:8000",
            "-v",
            f"{LOCAL_NIM_CACHE}:/opt/nim/.cache/",
            "nvcr.io/nim/black-forest-labs/flux.1-kontext-dev:latest",
        ]

        try:
            # Start the container in the background
            result = subprocess.run(
                podman_cmd, check=True, capture_output=True, text=True
            )
            logging.info(
                f"Flux Kontext NIM server start result: {result.stdout.strip()}"
            )

            return generate_success_response(
                "Flux Kontext NIM server started successfully."
            )

        except subprocess.CalledProcessError as e:
            logging.error(f"Error starting Flux Kontext NIM server: {e}")
            return generate_failure_response(
                f"Error starting Flux Kontext NIM server: {e}"
            )
        except FileNotFoundError:
            logging.error("WSL or podman command not found")
            return generate_failure_response("WSL or podman command not found")
        except Exception as e:
            logging.error(f"Unexpected error starting Flux Kontext NIM server: {e}")
            return generate_failure_response(
                f"Error starting Flux Kontext NIM server: {e}"
            )

    except Exception as e:
        logging.error(f"Error in start_flux_kontext_nim: {str(e)}")
        return generate_failure_response(f"Error in start_flux_kontext_nim: {str(e)}")


def generate_image_worker(
    prompt: str, output_dir: str, flux_url: str, nvidia_api_key: str
):
    """Background worker function to generate image"""
    try:
        logging.info(f"Starting background image generation for prompt: {prompt}")

        payload = {
            "height": 768,
            "width": 1344,
            "cfg_scale": 5,
            "mode": "base",
            "samples": 1,
            "seed": 0,  # random seed
            "steps": 50,
            "prompt": prompt,
        }

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {nvidia_api_key}",
        }

        logging.info(f"Sending request to Flux API: {flux_url}")
        logging.info(f"Payload: {payload}")

        # Convert payload to JSON
        json_payload = json.dumps(payload)

        # For NVIDIA API endpoints, use the URL as-is (it already includes the full endpoint)
        # For local NIM servers, append /v1/infer to the base URL
        if flux_url.startswith("https://ai.api.nvidia.com"):
            FLUX_INFER_URL = flux_url
        else:
            FLUX_INFER_URL = f"{flux_url}/v1/infer"

        # Create request
        req = urllib.request.Request(
            FLUX_INFER_URL,
            data=json_payload.encode("utf-8"),
            headers=headers,
            method="POST",
        )

        # Send request
        with urllib.request.urlopen(
            req, timeout=300
        ) as response:  # Increased timeout to 5 minutes
            response_data = json.loads(response.read().decode("utf-8"))
            logging.info(f"Flux API response received.")

            if "artifacts" in response_data and len(response_data["artifacts"]) > 0:
                artifact = response_data["artifacts"][0]
                image_data = artifact["base64"]

                import datetime

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"flux_image_{timestamp}.png"
                file_path = os.path.join(output_dir, filename)

                # Save the image
                import base64

                image_bytes = base64.b64decode(image_data)

                with open(file_path, "wb") as f:
                    f.write(image_bytes)

                logging.info(f"Image saved successfully: {file_path}")

                # Set the image as desktop background
                if set_desktop_background(file_path):
                    logging.info(f"Successfully set {file_path} as desktop background")
                else:
                    logging.warning(f"Failed to set {file_path} as desktop background")
            else:
                logging.error("No artifacts found in response")

    except urllib.error.URLError as e:
        logging.error(f"Error making request to Flux API: {e}")
    except urllib.error.HTTPError as e:
        logging.error(f"HTTP error from Flux API: {e}")
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing API response: {e}")
    except Exception as e:
        logging.error(f"Unexpected error during image generation: {e}")


def generate_image(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `generate_image` function

    Generates an image using the Flux NIM API in a background thread.

    Args:
        params: Function parameters (can include 'prompt')
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing generate_image with params: {params}")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        # Check if NVIDIA API key is configured (only required for NVIDIA API endpoints)
        global NVIDIA_API_KEY, FLUX_NIM_URL
        if FLUX_NIM_URL.startswith("https://ai.api.nvidia.com"):
            if (
                not NVIDIA_API_KEY
                or NVIDIA_API_KEY == "YOUR_NVIDIA_API_KEY_HERE"
                or not NVIDIA_API_KEY.startswith("nvapi-")
            ):
                return generate_failure_response(
                    'NVIDIA API key not configured or invalid. Please set a valid NVIDIA_API_KEY (starting with "nvapi-") in config.json'
                )

        # Get prompt from parameters (optional)
        prompt = params.get("prompt", "") if params else ""
        if not prompt:
            prompt = "A beautiful landscape with mountains and a lake"
            logging.info(f"No prompt provided, using default: {prompt}")
        else:
            logging.info(f"Using provided prompt: {prompt}")

        # Ensure output directory exists
        global OUTPUT_DIRECTORY
        try:
            os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)
            logging.info(f"Output directory: {OUTPUT_DIRECTORY}")
        except (OSError, PermissionError) as e:
            error_msg = f"Failed to create output directory '{OUTPUT_DIRECTORY}': {e}. Please check the path and permissions."
            logging.error(error_msg)
            return generate_failure_response(error_msg)

        # Start image generation in background thread
        thread = threading.Thread(
            target=generate_image_worker,
            args=(prompt, OUTPUT_DIRECTORY, FLUX_NIM_URL, NVIDIA_API_KEY),
            daemon=True,
        )
        thread.start()

        logging.info(f"Started background image generation thread for prompt: {prompt}")
        return generate_success_response(
            f'Your image generation request is in progress! Prompt: "{prompt}"'
        )

    except Exception as e:
        logging.error(f"Error in generate_image: {str(e)}")
        return generate_failure_response(f"Error in generate_image: {str(e)}")


def find_most_recent_image(directory: str, extensions: set[str]):
    """
    Recursively search for the most recent image file in a directory.

    Args:
        directory (str): Root directory to search in.
        extensions (set[str]): Set of allowed image file extensions (e.g., {'.png', '.jpg'}).

    Returns:
        Optional[str]: Path to the most recently modified image file, or None if not found.
    """
    from pathlib import Path

    dir_path = Path(directory)
    if not dir_path.exists() or not dir_path.is_dir():
        logging.warning(f"Directory does not exist or is not a directory: {directory}")
        return None

    latest_file = None
    latest_mtime = 0

    try:
        for file_path in dir_path.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in extensions:
                mtime = file_path.stat().st_mtime
                if mtime > latest_mtime:
                    latest_file = str(file_path)
                    latest_mtime = mtime
    except Exception as e:
        logging.error(f"Error while scanning directory {directory}: {e}")
        return None

    return latest_file


def upload_image_to_invoke(image_path: str, invokeai_url: str, board_id: str = None):
    """
    Uploads an image to InvokeAI and returns the image name.

    Args:
        image_path (str): Path to the image file
        invokeai_url (str): Base URL for InvokeAI
        board_id (str): ID of the board to upload to (optional)

    Returns:
        str: The image name returned by InvokeAI, or None if upload failed
    """
    upload_url = f"{invokeai_url}/api/v1/images/upload"
    params = {
        "image_category": "user",
        "is_intermediate": "false",
        "crop_visible": "false",
    }

    # Add board_id to params if provided
    if board_id:
        params["board_id"] = board_id

    try:
        # Get the MIME type of the image
        mime_type, _ = mimetypes.guess_type(image_path)
        if mime_type is None:
            mime_type = "image/png"  # Default to PNG if guess fails

        # Prepare the file for upload
        with open(image_path, "rb") as f:
            files = {
                "file": (os.path.basename(image_path), f, mime_type),
                # 'resize_to': (None, '(1360,768)')
            }

            # Make the request
            response = requests.post(
                upload_url,
                params=params,
                files=files,
                headers={"accept": "application/json"},
            )

            response.raise_for_status()
            result = response.json()
            return result.get("image_name")

    except Exception as e:
        logging.error(f"Error uploading image to InvokeAI: {e}")
        return None


# This dictionary defines the workflow that will be sent to InvokeAI for doing Flux Kontext generation
INVOKEAI_FLUX_KONTEXT_WORKFLOW = {
    "queue_id": "default",
    "enqueued": 0,
    "requested": 0,
    "batch": {
        "data": [],
        "graph": {
            "id": "ec50dc0e-363b-4723-bf89-264cf52a4af1",
            "nodes": {
                "flux_model_loader:ywdpEhgSIn": {
                    "id": "flux_model_loader:ywdpEhgSIn",
                    "is_intermediate": True,
                    "use_cache": True,
                    "model": {
                        "key": "c5ba7675-db0c-4280-a426-0154b5de8c98",
                        "hash": "blake3:8aadbed066021cc98686965fe9fc580083acbe027f600190f8d2436e6ac8b366",
                        "name": "FLUX.1 Kontext dev (Quantized)",
                        "base": "flux",
                        "type": "main",
                    },
                    "t5_encoder_model": {
                        "key": "284404cd-baf2-42cc-bbb3-a430d8909df6",
                        "hash": "blake3:12f3f5d4856e684c627c0b5c403ace83a8e8baaf0fa6518cd230b5ec1c519107",
                        "name": "t5_base_encoder",
                        "base": "any",
                        "type": "t5_encoder",
                    },
                    "clip_embed_model": {
                        "key": "f4269feb-2e98-4174-9c22-74dca9140584",
                        "hash": "blake3:17c19f0ef941c3b7609a9c94a659ca5364de0be364a91d4179f0e39ba17c3b70",
                        "name": "clip-vit-large-patch14",
                        "base": "any",
                        "type": "clip_embed",
                    },
                    "vae_model": {
                        "key": "0f0ccb31-5bd9-4a29-b2a4-56168596f4d6",
                        "hash": "blake3:ce21cb76364aa6e2421311cf4a4b5eb052a76c4f1cd207b50703d8978198a068",
                        "name": "FLUX.1-schnell_ae",
                        "base": "flux",
                        "type": "vae",
                    },
                    "type": "flux_model_loader",
                },
                "positive_prompt:0oQdkhpu9K": {
                    "id": "positive_prompt:0oQdkhpu9K",
                    "is_intermediate": True,
                    "use_cache": True,
                    "value": "make it in the style of studio ghibli anime",
                    "type": "string",
                },
                "flux_text_encoder:o0tHGDGa69": {
                    "id": "flux_text_encoder:o0tHGDGa69",
                    "is_intermediate": True,
                    "use_cache": True,
                    "type": "flux_text_encoder",
                },
                "pos_cond_collect:ApPpdRqgK2": {
                    "id": "pos_cond_collect:ApPpdRqgK2",
                    "is_intermediate": True,
                    "use_cache": True,
                    "collection": [],
                    "type": "collect",
                },
                "seed:aVE0l2Zlf1": {
                    "id": "seed:aVE0l2Zlf1",
                    "is_intermediate": True,
                    "use_cache": True,
                    "value": 1234,
                    "type": "integer",
                },
                "flux_denoise:9SHZg1d4kC": {
                    "id": "flux_denoise:9SHZg1d4kC",
                    "is_intermediate": True,
                    "use_cache": True,
                    "denoising_start": 0,
                    "denoising_end": 1,
                    "add_noise": True,
                    "cfg_scale": 1,
                    "cfg_scale_start_step": 0,
                    "cfg_scale_end_step": -1,
                    "width": 1376,
                    "height": 784,
                    "num_steps": 50,
                    "guidance": 9,
                    "seed": 0,
                    "type": "flux_denoise",
                },
                "flux_vae_decode:Vr4fbsSEgU": {
                    "id": "flux_vae_decode:Vr4fbsSEgU",
                    "is_intermediate": True,
                    "use_cache": True,
                    "type": "flux_vae_decode",
                },
                "core_metadata:oCIejDlaQA": {
                    "id": "core_metadata:oCIejDlaQA",
                    "is_intermediate": True,
                    "use_cache": True,
                    "generation_mode": "flux_txt2img",
                    "width": 1360,
                    "height": 768,
                    "steps": 50,
                    "model": {
                        "key": "c5ba7675-db0c-4280-a426-0154b5de8c98",
                        "hash": "blake3:8aadbed066021cc98686965fe9fc580083acbe027f600190f8d2436e6ac8b366",
                        "name": "FLUX.1 Kontext dev (Quantized)",
                        "base": "flux",
                        "type": "main",
                    },
                    "vae": {
                        "key": "0f0ccb31-5bd9-4a29-b2a4-56168596f4d6",
                        "hash": "blake3:ce21cb76364aa6e2421311cf4a4b5eb052a76c4f1cd207b50703d8978198a068",
                        "name": "FLUX.1-schnell_ae",
                        "base": "flux",
                        "type": "vae",
                    },
                    "type": "core_metadata",
                },
                "flux_kontext:MsQ9ynwazR": {
                    "id": "flux_kontext:MsQ9ynwazR",
                    "is_intermediate": True,
                    "use_cache": True,
                    "image": {
                        "image_name": "PLACEHOLDER.png"
                    },  # this will be replaced with the actual image name
                    "type": "flux_kontext",
                },
                "canvas_output:FhpncF2ITc": {
                    "id": "canvas_output:FhpncF2ITc",
                    "is_intermediate": False,
                    "use_cache": False,
                    "width": 1360,
                    "height": 768,
                    "resample_mode": "bicubic",
                    "type": "img_resize",
                },
            },
            "edges": [
                {
                    "source": {
                        "node_id": "flux_model_loader:ywdpEhgSIn",
                        "field": "transformer",
                    },
                    "destination": {
                        "node_id": "flux_denoise:9SHZg1d4kC",
                        "field": "transformer",
                    },
                },
                {
                    "source": {
                        "node_id": "flux_model_loader:ywdpEhgSIn",
                        "field": "vae",
                    },
                    "destination": {
                        "node_id": "flux_denoise:9SHZg1d4kC",
                        "field": "controlnet_vae",
                    },
                },
                {
                    "source": {
                        "node_id": "flux_model_loader:ywdpEhgSIn",
                        "field": "vae",
                    },
                    "destination": {
                        "node_id": "flux_vae_decode:Vr4fbsSEgU",
                        "field": "vae",
                    },
                },
                {
                    "source": {
                        "node_id": "flux_model_loader:ywdpEhgSIn",
                        "field": "clip",
                    },
                    "destination": {
                        "node_id": "flux_text_encoder:o0tHGDGa69",
                        "field": "clip",
                    },
                },
                {
                    "source": {
                        "node_id": "flux_model_loader:ywdpEhgSIn",
                        "field": "t5_encoder",
                    },
                    "destination": {
                        "node_id": "flux_text_encoder:o0tHGDGa69",
                        "field": "t5_encoder",
                    },
                },
                {
                    "source": {
                        "node_id": "flux_model_loader:ywdpEhgSIn",
                        "field": "max_seq_len",
                    },
                    "destination": {
                        "node_id": "flux_text_encoder:o0tHGDGa69",
                        "field": "t5_max_seq_len",
                    },
                },
                {
                    "source": {
                        "node_id": "positive_prompt:0oQdkhpu9K",
                        "field": "value",
                    },
                    "destination": {
                        "node_id": "flux_text_encoder:o0tHGDGa69",
                        "field": "prompt",
                    },
                },
                {
                    "source": {
                        "node_id": "flux_text_encoder:o0tHGDGa69",
                        "field": "conditioning",
                    },
                    "destination": {
                        "node_id": "pos_cond_collect:ApPpdRqgK2",
                        "field": "item",
                    },
                },
                {
                    "source": {
                        "node_id": "pos_cond_collect:ApPpdRqgK2",
                        "field": "collection",
                    },
                    "destination": {
                        "node_id": "flux_denoise:9SHZg1d4kC",
                        "field": "positive_text_conditioning",
                    },
                },
                {
                    "source": {"node_id": "seed:aVE0l2Zlf1", "field": "value"},
                    "destination": {
                        "node_id": "flux_denoise:9SHZg1d4kC",
                        "field": "seed",
                    },
                },
                {
                    "source": {
                        "node_id": "flux_denoise:9SHZg1d4kC",
                        "field": "latents",
                    },
                    "destination": {
                        "node_id": "flux_vae_decode:Vr4fbsSEgU",
                        "field": "latents",
                    },
                },
                {
                    "source": {"node_id": "seed:aVE0l2Zlf1", "field": "value"},
                    "destination": {
                        "node_id": "core_metadata:oCIejDlaQA",
                        "field": "seed",
                    },
                },
                {
                    "source": {
                        "node_id": "positive_prompt:0oQdkhpu9K",
                        "field": "value",
                    },
                    "destination": {
                        "node_id": "core_metadata:oCIejDlaQA",
                        "field": "positive_prompt",
                    },
                },
                {
                    "source": {
                        "node_id": "flux_kontext:MsQ9ynwazR",
                        "field": "kontext_cond",
                    },
                    "destination": {
                        "node_id": "flux_denoise:9SHZg1d4kC",
                        "field": "kontext_conditioning",
                    },
                },
                {
                    "source": {
                        "node_id": "flux_vae_decode:Vr4fbsSEgU",
                        "field": "image",
                    },
                    "destination": {
                        "node_id": "canvas_output:FhpncF2ITc",
                        "field": "image",
                    },
                },
                {
                    "source": {
                        "node_id": "core_metadata:oCIejDlaQA",
                        "field": "metadata",
                    },
                    "destination": {
                        "node_id": "canvas_output:FhpncF2ITc",
                        "field": "metadata",
                    },
                },
            ],
        },
        "runs": 1,
    },
    "priority": 0,
}


def modify_workflow_for_kontext(workflow_data, prompt, image_name):
    """
    Modifies the workflow data with the provided prompt and image name.

    Args:
        workflow_data (dict): The workflow dictionary to modify
        prompt (str): The prompt to use for generation
        image_name (str): The name of the uploaded image

    Returns:
        dict: The modified workflow data
    """
    try:
        # Create a deep copy of the workflow to avoid modifying the original
        import copy

        modified_workflow = copy.deepcopy(workflow_data)

        # Update the prompt node
        prompt_node_id = "positive_prompt:0oQdkhpu9K"
        modified_workflow["batch"]["graph"]["nodes"][prompt_node_id]["value"] = prompt
        logging.info(f"Updated prompt to: {prompt}")

        # Update the kontext node with the uploaded image
        kontext_node_id = "flux_kontext:MsQ9ynwazR"
        modified_workflow["batch"]["graph"]["nodes"][kontext_node_id]["image"][
            "image_name"
        ] = image_name
        logging.info(f"Updated kontext image to: {image_name}")

        return modified_workflow

    except Exception as e:
        logging.error(f"Error modifying workflow: {e}")
        return None


def submit_workflow_to_invokeai(workflow_data, invokeai_url):
    """
    Submits the modified workflow data to the InvokeAI API endpoint.

    Args:
        workflow_data (dict): The workflow data to submit
        invokeai_url (str): The base URL for InvokeAI

    Returns:
        bool: True on success, False on failure
    """
    api_endpoint = f"{invokeai_url}/api/v1/queue/default/enqueue_batch"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    logging.info(f"Submitting workflow to InvokeAI API at {api_endpoint}...")

    try:
        response = requests.post(
            api_endpoint, json=workflow_data, headers=headers, timeout=60
        )

        # Check for HTTP errors
        response.raise_for_status()

        logging.info("Workflow submitted successfully to the queue!")
        logging.info(f"API Response Status Code: {response.status_code}")

        try:
            response_json = response.json()
            # logging.info(f"API Response: {json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            logging.info(f"API Response (non-JSON): {response.text}")

        return True

    except requests.exceptions.ConnectionError as e:
        logging.error(f"Could not connect to the InvokeAI server at {invokeai_url}")
        logging.error(f"Details: {e}")
        return False
    except requests.exceptions.Timeout:
        logging.error("The request to the InvokeAI server timed out after 60 seconds")
        return False
    except requests.exceptions.HTTPError as e:
        logging.error(
            f"InvokeAI API request failed with status code {e.response.status_code}"
        )
        logging.error(f"URL: {e.request.url}")
        try:
            error_json = e.response.json()
            logging.error(f"Error Response: {json.dumps(error_json, indent=2)}")
        except json.JSONDecodeError:
            logging.error(f"Error Response: {e.response.text}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred during API submission: {e}")
        return False


def generate_image_using_kontext_worker(
    GALLERY_DIRECTORY: str,
    invokeai_url: str,
    board_id: str = None,
    prompt: str = None,
    steps: int = 30,
):
    """Background worker function to upload screenshot and process with InvokeAI"""
    try:
        logging.info(
            f"Starting background image generation using kontext from directory: {GALLERY_DIRECTORY}"
        )

        # Use default prompt if none provided
        if not prompt:
            prompt = "make it in the style of studio ghibli anime"
            logging.info(f"No prompt provided, using default: {prompt}")
        else:
            logging.info(f"Using provided prompt: {prompt}")

        # Look for common screenshot file extensions
        screenshot_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}

        # Find the most recent screenshot recursively
        latest_screenshot_path = find_most_recent_image(
            GALLERY_DIRECTORY, screenshot_extensions
        )

        if not latest_screenshot_path:
            logging.error(
                f"No screenshot files found in directory or subdirectories: {GALLERY_DIRECTORY}"
            )
            return

        logging.info(f"Using most recent screenshot: {latest_screenshot_path}")

        # Step 1: Upload the image using the requests library
        image_name = upload_image_to_invoke(
            latest_screenshot_path, invokeai_url, board_id
        )

        if not image_name:
            logging.error("Failed to upload image to InvokeAI")
            return

        logging.info(f"Successfully uploaded image with name: {image_name}")

        # Step 2: Modify the workflow with the prompt and image name
        modified_workflow = modify_workflow_for_kontext(
            INVOKEAI_FLUX_KONTEXT_WORKFLOW, prompt, image_name
        )

        if not modified_workflow:
            logging.error("Failed to modify workflow")
            return

        # Step 3: Submit the workflow to InvokeAI
        success = submit_workflow_to_invokeai(modified_workflow, invokeai_url)

        if success:
            logging.info("Successfully submitted Flux Kontext workflow to InvokeAI")
        else:
            logging.error("Failed to submit workflow to InvokeAI")

    except Exception as e:
        logging.error(f"Unexpected error during Flux Kontext generation: {e}")


def generate_image_using_kontext_nim_worker(
    gallery_directory: str,
    flux_kontext_nim_url: str,
    prompt: str = None,
    steps: int = 30,
):
    """Background worker function to process screenshot with Flux Kontext NIM"""
    try:
        logging.info(
            f"Starting background image generation using Flux Kontext NIM from directory: {gallery_directory}"
        )

        # Ensure output directory exists
        global OUTPUT_DIRECTORY
        try:
            os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)
            logging.info(f"Output directory: {OUTPUT_DIRECTORY}")
        except (OSError, PermissionError) as e:
            error_msg = f"Failed to create output directory '{OUTPUT_DIRECTORY}': {e}. Please check the path and permissions."
            logging.error(error_msg)
            return

        # Use default prompt if none provided
        if not prompt:
            prompt = "make it in the style of studio ghibli anime"
            logging.info(f"No prompt provided, using default: {prompt}")
        else:
            logging.info(f"Using provided prompt: {prompt}")

        # Look for common screenshot file extensions
        screenshot_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}

        # Find the most recent screenshot recursively
        latest_screenshot_path = find_most_recent_image(
            gallery_directory, screenshot_extensions
        )

        if not latest_screenshot_path:
            logging.error(
                f"No screenshot files found in directory or subdirectories: {gallery_directory}"
            )
            return

        logging.info(f"Using most recent screenshot: {latest_screenshot_path}")

        # Step 1: Prepare the image (scale/crop to 1392x752 and convert to base64)
        base64_image = prepare_image_for_kontext(latest_screenshot_path)

        if not base64_image:
            logging.error("Failed to prepare image for Flux Kontext NIM")
            return

        logging.info("Successfully prepared image for Flux Kontext NIM")

        # Step 2: Send request to Flux Kontext NIM
        payload = {
            "prompt": prompt,
            "image": base64_image,
            "cfg_scale": 3.5,
            "aspect_ratio": "match_input_image",
            "samples": 1,
            "seed": 0,  # random seed
            "steps": steps,  # use the steps parameter
        }

        headers = {"accept": "application/json", "content-type": "application/json"}

        logging.info(f"Sending request to Flux Kontext NIM: {flux_kontext_nim_url}")

        # Create a truncated payload for logging (base64 data is very long)
        log_payload = payload.copy()
        if "image" in log_payload and log_payload["image"].startswith(
            "data:image/png;base64,"
        ):
            base64_data = log_payload["image"]
            truncated = (
                base64_data[:50] + "..." + base64_data[-20:]
            )  # Show first 50 and last 20 chars
            log_payload["image"] = truncated

        logging.info(f"Payload: {log_payload}")

        # Construct the inference endpoint URL
        inference_url = f"{flux_kontext_nim_url}/v1/infer"
        logging.info(f"Sending request to inference endpoint: {inference_url}")

        response = requests.post(
            inference_url, json=payload, headers=headers, timeout=300
        )

        # Check for HTTP errors
        response.raise_for_status()

        # Parse the response
        response_data = response.json()
        logging.info("Flux Kontext NIM response received")

        if "artifacts" in response_data and len(response_data["artifacts"]) > 0:
            artifact = response_data["artifacts"][0]
            image_data = artifact["base64"]

            # Create output filename with timestamp
            import datetime

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"flux_kontext_nim_{timestamp}.png"
            file_path = os.path.join(OUTPUT_DIRECTORY, filename)

            # Save the image
            image_bytes = base64.b64decode(image_data)

            with open(file_path, "wb") as f:
                f.write(image_bytes)

            logging.info(f"Image saved successfully: {file_path}")

            # Set the image as desktop background
            if set_desktop_background(file_path):
                logging.info(f"Successfully set {file_path} as desktop background")
            else:
                logging.warning(f"Failed to set {file_path} as desktop background")
        else:
            logging.error("No artifacts found in Flux Kontext NIM response")

    except requests.exceptions.ConnectionError as e:
        logging.error(
            f"Could not connect to Flux Kontext NIM server at {flux_kontext_nim_url}: {e}"
        )
    except requests.exceptions.Timeout:
        logging.error("Request to Flux Kontext NIM server timed out after 5 minutes")
    except requests.exceptions.HTTPError as e:
        logging.error(
            f"Flux Kontext NIM API request failed with status code {e.response.status_code}"
        )
        # Log the full error response to see what the server is complaining about
        try:
            error_response = e.response.json()
            logging.error(
                f"Error response from server: {json.dumps(error_response, indent=2)}"
            )
        except json.JSONDecodeError:
            logging.error(f"Error response text: {e.response.text}")
        logging.error(f"Request URL: {e.request.url}")
        logging.error(f"Request headers: {dict(e.request.headers)}")
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing Flux Kontext NIM response: {e}")
    except Exception as e:
        logging.error(f"Unexpected error during Flux Kontext NIM generation: {e}")


def generate_image_using_kontext(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `generate_image_using_kontext` function

    Uploads the most recent screenshot from GALLERY_DIRECTORY and runs Flux Kontext workflow.
    Chooses between Flux Kontext NIM and InvokeAI backends based on configuration.

    Args:
        params: Function parameters (can include 'prompt' and 'steps')
        context: Context information
        system_info: System information

    Returns:
        The function return value(s)
    """
    logging.info(f"Executing generate_image_using_kontext with params: {params}")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        # Check if GALLERY_DIRECTORY is configured
        global GALLERY_DIRECTORY, FLUX_KONTEXT_NIM_URL, INVOKEAI_URL, COMFYUI_URL, FLUX_KONTEXT_INFERENCE_BACKEND, BOARD_ID
        if not GALLERY_DIRECTORY:
            return generate_failure_response(
                "GALLERY_DIRECTORY not configured. Please set GALLERY_DIRECTORY in config.json"
            )

        # Get parameters from params (optional)
        prompt = params.get("prompt", "") if params else ""
        steps = params.get("steps", 30) if params else 30  # Default to 30 steps

        # Validate steps parameter
        try:
            steps = int(steps)
            if steps < 20 or steps > 50:
                return generate_failure_response(
                    "Steps parameter must be between 20 and 50"
                )
        except (ValueError, TypeError):
            return generate_failure_response("Steps parameter must be an integer")

        # Determine which backend to use based on FLUX_KONTEXT_INFERENCE_BACKEND configuration
        backend = FLUX_KONTEXT_INFERENCE_BACKEND.upper()

        # Validate that the chosen backend has a valid URL configuration
        if backend == "NIM":
            if not FLUX_KONTEXT_NIM_URL:
                return generate_failure_response(
                    f"FLUX_KONTEXT_INFERENCE_BACKEND is set to 'NIM' but FLUX_KONTEXT_NIM_URL is not configured. Please set FLUX_KONTEXT_NIM_URL in config.json"
                )

            # Use Flux Kontext NIM backend
            logging.info(f"Using Flux Kontext NIM backend at: {FLUX_KONTEXT_NIM_URL}")

            # Start Flux Kontext NIM generation in background thread
            thread = threading.Thread(
                target=generate_image_using_kontext_nim_worker,
                args=(GALLERY_DIRECTORY, FLUX_KONTEXT_NIM_URL, prompt, steps),
                daemon=True,
            )
            thread.start()

            if prompt:
                logging.info(
                    f"Started background Flux Kontext NIM generation thread with prompt: {prompt}"
                )
                return generate_success_response(
                    f'Your Flux Kontext NIM generation request is in progress! Using screenshot from: {GALLERY_DIRECTORY} with prompt: "{prompt}"'
                )
            else:
                logging.info(
                    f"Started background Flux Kontext NIM generation thread with default prompt"
                )
                return generate_success_response(
                    f"Your Flux Kontext NIM generation request is in progress! Using screenshot from: {GALLERY_DIRECTORY}"
                )

        elif backend == "INVOKEAI":
            if not INVOKEAI_URL:
                return generate_failure_response(
                    f"FLUX_KONTEXT_INFERENCE_BACKEND is set to 'INVOKEAI' but INVOKEAI_URL is not configured. Please set INVOKEAI_URL in config.json"
                )

            # Use InvokeAI backend
            logging.info(f"Using InvokeAI backend at: {INVOKEAI_URL}")

            # Start InvokeAI generation in background thread
            thread = threading.Thread(
                target=generate_image_using_kontext_worker,
                args=(GALLERY_DIRECTORY, INVOKEAI_URL, BOARD_ID, prompt, steps),
                daemon=True,
            )
            thread.start()

            if prompt:
                logging.info(
                    f"Started background InvokeAI Flux Kontext generation thread with prompt: {prompt}"
                )
                return generate_success_response(
                    f'Your InvokeAI Flux Kontext generation request is in progress! Using screenshot from: {GALLERY_DIRECTORY} with prompt: "{prompt}"'
                )
            else:
                logging.info(
                    f"Started background InvokeAI Flux Kontext generation thread with default prompt"
                )
                return generate_success_response(
                    f"Your InvokeAI Flux Kontext generation request is in progress! Using screenshot from: {GALLERY_DIRECTORY}"
                )

        elif backend == "COMFYUI":
            if not COMFYUI_URL:
                return generate_failure_response(
                    f"FLUX_KONTEXT_INFERENCE_BACKEND is set to 'COMFYUI' but COMFYUI_URL is not configured. Please set COMFYUI_URL in config.json"
                )

            # Use ComfyUI backend
            logging.info(f"Using ComfyUI backend at: {COMFYUI_URL}")

            # Start ComfyUI generation in background thread
            thread = threading.Thread(
                target=generate_image_using_comfyui_worker,
                args=(GALLERY_DIRECTORY, COMFYUI_URL, prompt, steps),
                daemon=True,
            )
            thread.start()

            if prompt:
                logging.info(
                    f"Started background ComfyUI Flux Kontext generation thread with prompt: {prompt}"
                )
                return generate_success_response(
                    f'Your ComfyUI Flux Kontext generation request is in progress! Using screenshot from: {GALLERY_DIRECTORY} with prompt: "{prompt}"'
                )
            else:
                logging.info(
                    f"Started background ComfyUI Flux Kontext generation thread with default prompt"
                )
                return generate_success_response(
                    f"Your ComfyUI Flux Kontext generation request is in progress! Using screenshot from: {GALLERY_DIRECTORY}"
                )

        else:
            # Invalid backend configuration
            return generate_failure_response(
                f"Invalid FLUX_KONTEXT_INFERENCE_BACKEND value: '{FLUX_KONTEXT_INFERENCE_BACKEND}'. Must be one of: NIM, INVOKEAI, COMFYUI"
            )

    except Exception as e:
        logging.error(f"Error in generate_image_using_kontext: {str(e)}")
        return generate_failure_response(
            f"Error in generate_image_using_kontext: {str(e)}"
        )


def invokeai_status(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `invokeai_status` function

    Checks the status of the InvokeAI service by calling the /api/v1/app/version endpoint.

    Args:
        params: Function parameters (not used)
        context: Context information (not used)
        system_info: System information (not used)

    Returns:
        The function return value with InvokeAI version information
    """
    logging.info("Executing invokeai_status")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        global INVOKEAI_URL

        # Construct the version endpoint URL
        version_url = f"{INVOKEAI_URL}/api/v1/app/version"

        logging.info(f"Checking InvokeAI status at: {version_url}")

        # Make the request to the version endpoint
        response = requests.get(version_url, timeout=10)

        # Check for HTTP errors
        response.raise_for_status()

        # Parse the JSON response
        version_data = response.json()

        # Extract the version from the response
        version = version_data.get("version", "Unknown")
        highlights = version_data.get("highlights", [])

        logging.info(f"InvokeAI version: {version}")

        # Create a formatted response message
        message = f"InvokeAI service is running. Version: {version}"

        if highlights:
            message += f"\nHighlights: {', '.join(highlights)}"

        return generate_success_response(message)

    except requests.exceptions.ConnectionError:
        error_msg = f"Could not connect to InvokeAI server at {INVOKEAI_URL}. Is the service running?"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.Timeout:
        error_msg = "Request to InvokeAI server timed out"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.HTTPError as e:
        error_msg = (
            f"InvokeAI API request failed with status code {e.response.status_code}"
        )
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except json.JSONDecodeError as e:
        error_msg = f"Failed to parse InvokeAI response: {e}"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error checking InvokeAI status: {str(e)}"
        logging.error(error_msg)
        return generate_failure_response(error_msg)


def pause_invokeai_processor(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `pause_invokeai_processor` function

    Pauses the InvokeAI processor by calling the /api/v1/queue/default/processor/pause endpoint.

    Args:
        params: Function parameters (not used)
        context: Context information (not used)
        system_info: System information (not used)

    Returns:
        The function return value indicating success or failure
    """
    logging.info("Executing pause_invokeai_processor")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        global INVOKEAI_URL

        # Construct the pause endpoint URL
        pause_url = f"{INVOKEAI_URL}/api/v1/queue/default/processor/pause"

        logging.info(f"Pausing InvokeAI processor at: {pause_url}")

        # Make the PUT request to pause the processor
        response = requests.put(pause_url, timeout=10)

        # Check for HTTP errors
        response.raise_for_status()

        logging.info("Successfully paused InvokeAI processor")

        return generate_success_response(
            "InvokeAI processor has been paused successfully"
        )

    except requests.exceptions.ConnectionError:
        error_msg = f"Could not connect to InvokeAI server at {INVOKEAI_URL}. Is the service running?"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.Timeout:
        error_msg = "Request to InvokeAI server timed out"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.HTTPError as e:
        error_msg = (
            f"InvokeAI API request failed with status code {e.response.status_code}"
        )
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error pausing InvokeAI processor: {str(e)}"
        logging.error(error_msg)
        return generate_failure_response(error_msg)


def resume_invokeai_processor(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `resume_invokeai_processor` function

    Resumes the InvokeAI processor by calling the /api/v1/queue/default/processor/resume endpoint.

    Args:
        params: Function parameters (not used)
        context: Context information (not used)
        system_info: System information (not used)

    Returns:
        The function return value indicating success or failure
    """
    logging.info("Executing resume_invokeai_processor")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        global INVOKEAI_URL

        # Construct the resume endpoint URL
        resume_url = f"{INVOKEAI_URL}/api/v1/queue/default/processor/resume"

        logging.info(f"Resuming InvokeAI processor at: {resume_url}")

        # Make the PUT request to resume the processor
        response = requests.put(resume_url, timeout=10)

        # Check for HTTP errors
        response.raise_for_status()

        logging.info("Successfully resumed InvokeAI processor")

        return generate_success_response(
            "InvokeAI processor has been resumed successfully"
        )

    except requests.exceptions.ConnectionError:
        error_msg = f"Could not connect to InvokeAI server at {INVOKEAI_URL}. Is the service running?"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.Timeout:
        error_msg = "Request to InvokeAI server timed out"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.HTTPError as e:
        error_msg = (
            f"InvokeAI API request failed with status code {e.response.status_code}"
        )
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error resuming InvokeAI processor: {str(e)}"
        logging.error(error_msg)
        return generate_failure_response(error_msg)


def invokeai_empty_model_cache(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `invokeai_empty_model_cache` function

    Empties the InvokeAI model cache to free up VRAM by calling the /api/v2/models/empty_model_cache endpoint.

    Args:
        params: Function parameters (not used)
        context: Context information (not used)
        system_info: System information (not used)

    Returns:
        The function return value indicating success or failure
    """
    logging.info("Executing invokeai_empty_model_cache")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        global INVOKEAI_URL

        # Construct the empty model cache endpoint URL
        empty_cache_url = f"{INVOKEAI_URL}/api/v2/models/empty_model_cache"

        logging.info(f"Emptying InvokeAI model cache at: {empty_cache_url}")

        # Make the POST request to empty the model cache
        response = requests.post(empty_cache_url, timeout=30)

        # Check for HTTP errors
        response.raise_for_status()

        logging.info("Successfully emptied InvokeAI model cache")

        return generate_success_response(
            "InvokeAI model cache has been emptied successfully"
        )

    except requests.exceptions.ConnectionError:
        error_msg = f"Could not connect to InvokeAI server at {INVOKEAI_URL}. Is the service running?"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.Timeout:
        error_msg = "Request to InvokeAI server timed out"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.HTTPError as e:
        error_msg = (
            f"InvokeAI API request failed with status code {e.response.status_code}"
        )
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error emptying InvokeAI model cache: {str(e)}"
        logging.error(error_msg)
        return generate_failure_response(error_msg)


COMFYUI_FLUX_KONTEXT_WORKFLOW = {
    "1": {
        "inputs": {"filename_prefix": "ComfyUI", "images": ["21", 0]},
        "class_type": "SaveImage",
        "_meta": {"title": "Save Image"},
    },
    "4": {
        "inputs": {},
        "class_type": "InstallNIMNode",
        "_meta": {"title": "Install NIM"},
    },
    "6": {
        "inputs": {
            "model_type": "FLUX_KONTEXT",
            "operation": "Start",
            "offloading_policy": "Default",
            "hf_token": ["8", 0],
            "is_nim_installed": ["4", 0],
        },
        "class_type": "LoadNIMNode",
        "_meta": {"title": "Load NIM"},
    },
    "8": {
        "inputs": {},
        "class_type": "Get_HFToken",
        "_meta": {"title": "Use HF_TOKEN EnVar"},
    },
    "11": {
        "inputs": {"image": "552ed252-7ea7-495f-98e6-2d11c992c8ac.png"},
        "class_type": "LoadImage",
        "_meta": {"title": "Load Image"},
    },
    "16": {
        "inputs": {"image": ["11", 0]},
        "class_type": "FluxKontextImageScale",
        "_meta": {"title": "FluxKontextImageScale"},
    },
    "21": {
        "inputs": {
            "width": ["22", 0],
            "height": ["22", 1],
            "prompt": "make into into fine Chinese porcelain blue and white",
            "cfg_scale": 2.5,
            "seed": 738487792,
            "steps": 20,
            "is_nim_started": ["6", 0],
            "image": ["16", 0],
        },
        "class_type": "NIMFLUXNode",
        "_meta": {"title": "NIM Generate"},
    },
    "22": {
        "inputs": {"image": ["16", 0]},
        "class_type": "GetImageSize",
        "_meta": {"title": "Get Image Size"},
    },
}


def generate_image_using_comfyui_worker(
    gallery_directory: str,
    comfyui_url: str,
    prompt: str = None,
    steps: int = 30,
    workflow: str = COMFYUI_FLUX_KONTEXT_WORKFLOW,
):
    """Background worker function to process screenshot with ComfyUI Flux Kontext workflow"""
    try:
        logging.info(
            f"Starting background image generation using ComfyUI from directory: {gallery_directory}"
        )

        # Ensure output directory exists
        global OUTPUT_DIRECTORY
        try:
            os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)
            logging.info(f"Output directory: {OUTPUT_DIRECTORY}")
        except (OSError, PermissionError) as e:
            error_msg = f"Failed to create output directory '{OUTPUT_DIRECTORY}': {e}. Please check the path and permissions."
            logging.error(error_msg)
            return

        # Use default prompt if none provided
        if not prompt:
            prompt = "make it in the style of studio ghibli anime"
            logging.info(f"No prompt provided, using default: {prompt}")
        else:
            logging.info(f"Using provided prompt: {prompt}")

        # Look for common screenshot file extensions
        screenshot_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}

        # Find the most recent screenshot recursively
        latest_screenshot_path = find_most_recent_image(
            gallery_directory, screenshot_extensions
        )

        if not latest_screenshot_path:
            logging.error(
                f"No screenshot files found in directory or subdirectories: {gallery_directory}"
            )
            return

        logging.info(f"Using most recent screenshot: {latest_screenshot_path}")

        # Step 1: Prepare the image (scale/crop to 1392x752 and save to temporary file)
        resized_image_path = prepare_image_for_comfyui(latest_screenshot_path)

        if not resized_image_path:
            logging.error("Failed to prepare image for ComfyUI Flux Kontext")
            return

        logging.info("Successfully prepared image for ComfyUI Flux Kontext")

        # Step 2: Upload the resized image to ComfyUI
        image_name = upload_image_to_comfyui(resized_image_path, comfyui_url)

        if not image_name:
            logging.error("Failed to upload image to ComfyUI")
            return

        logging.info(f"Successfully uploaded image to ComfyUI with name: {image_name}")

        # Step 3: Modify the workflow with the prompt, image name, and steps
        modified_workflow = modify_comfyui_workflow_for_kontext(
            COMFYUI_FLUX_KONTEXT_WORKFLOW, prompt, image_name, steps
        )

        if not modified_workflow:
            logging.error("Failed to modify ComfyUI workflow")
            return

        # Step 4: Execute the workflow via ComfyUI API
        logging.info("Executing ComfyUI workflow...")
        output_images = execute_comfyui_workflow(modified_workflow, comfyui_url)

        if output_images:
            logging.info("Successfully executed ComfyUI Flux Kontext workflow")

            # Save the output image
            for node_id, images in output_images.items():
                for i, image_data in enumerate(images):
                    # Create output filename with timestamp
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"comfyui_flux_kontext_{timestamp}_{i}.png"
                    file_path = os.path.join(OUTPUT_DIRECTORY, filename)

                    # Save the image
                    with open(file_path, "wb") as f:
                        f.write(image_data)

                    logging.info(f"Image saved successfully: {file_path}")

                    # Set the image as desktop background
                    if set_desktop_background(file_path):
                        logging.info(
                            f"Successfully set {file_path} as desktop background"
                        )
                    else:
                        logging.warning(
                            f"Failed to set {file_path} as desktop background"
                        )
        else:
            logging.error("Failed to execute ComfyUI workflow")

    except requests.exceptions.ConnectionError as e:
        logging.error(f"Could not connect to ComfyUI server at {comfyui_url}: {e}")
    except requests.exceptions.Timeout:
        logging.error("Request to ComfyUI server timed out after 5 minutes")
    except requests.exceptions.HTTPError as e:
        logging.error(
            f"ComfyUI API request failed with status code {e.response.status_code}"
        )
        # Log the full error response to see what the server is complaining about
        try:
            error_response = e.response.json()
            logging.error(
                f"Error response from server: {json.dumps(error_response, indent=2)}"
            )
        except json.JSONDecodeError:
            logging.error(f"Error response text: {e.response.text}")
        logging.error(f"Request URL: {e.request.url}")
        logging.error(f"Request headers: {dict(e.request.headers)}")
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing ComfyUI response: {e}")
    except Exception as e:
        logging.error(f"Unexpected error during ComfyUI generation: {e}")
    finally:
        # Clean up temporary files
        if "resized_image_path" in locals() and os.path.exists(resized_image_path):
            try:
                os.unlink(resized_image_path)
                logging.info(f"Cleaned up temporary file: {resized_image_path}")
            except Exception as e:
                logging.warning(
                    f"Failed to clean up temporary file {resized_image_path}: {e}"
                )


def upload_image_to_comfyui(image_path: str, comfyui_url: str):
    """
    Uploads an image to ComfyUI and returns the image name.

    Args:
        image_path (str): Path to the image file
        comfyui_url (str): Base URL for ComfyUI

    Returns:
        str: The image name returned by ComfyUI, or None if upload failed
    """
    upload_url = f"{comfyui_url}/upload/image"

    try:
        # Get the MIME type of the image
        mime_type, _ = mimetypes.guess_type(image_path)
        if mime_type is None:
            mime_type = "image/png"  # Default to PNG if guess fails

        # Prepare the file for upload
        with open(image_path, "rb") as f:
            files = {
                "image": (os.path.basename(image_path), f, mime_type),
            }

            # Make the request
            response = requests.post(
                upload_url,
                files=files,
                headers={"accept": "application/json"},
            )

            response.raise_for_status()
            result = response.json()
            return result.get("name")

    except Exception as e:
        logging.error(f"Error uploading image to ComfyUI: {e}")
        return None


def modify_comfyui_workflow_for_kontext(workflow_data, prompt, image_name, steps):
    """
    Modifies the ComfyUI workflow data with the provided prompt, image name, and steps.

    Args:
        workflow_data (dict): The workflow dictionary to modify
        prompt (str): The prompt to use for generation
        image_name (str): The name of the uploaded image
        steps (int): Number of inference steps

    Returns:
        dict: The modified workflow data
    """
    try:
        # Create a deep copy of the workflow to avoid modifying the original
        import copy

        modified_workflow = copy.deepcopy(workflow_data)

        # Debug: Log the workflow structure
        logging.info(f"Workflow has {len(modified_workflow)} nodes")

        # Log all available node IDs and class_types for debugging
        node_ids = list(modified_workflow.keys())
        node_types = [
            modified_workflow[node_id].get("class_type") for node_id in node_ids
        ]
        logging.info(f"Available node IDs: {node_ids}")
        logging.info(f"Available node class_types: {node_types}")

        # Find the NIMFLUXNode and LoadImage nodes by class_type
        nimflux_node_id = None
        loadimage_node_id = None

        for node_id, node_data in modified_workflow.items():
            if node_data.get("class_type") == "NIMFLUXNode":
                nimflux_node_id = node_id
                logging.info(f"Found NIMFLUXNode with ID: {node_id}")
            elif node_data.get("class_type") == "LoadImage":
                loadimage_node_id = node_id
                logging.info(f"Found LoadImage node with ID: {node_id}")

        if not nimflux_node_id:
            logging.error("NIMFLUXNode not found in workflow")
            return None

        if not loadimage_node_id:
            logging.error("LoadImage node not found in workflow")
            return None

        # Get the node data
        nimflux_node = modified_workflow[nimflux_node_id]
        loadimage_node = modified_workflow[loadimage_node_id]

        # Check if NIMFLUXNode has the expected structure
        if "inputs" not in nimflux_node:
            logging.error("NIMFLUXNode missing inputs")
            return None

        # Update the prompt in the NIMFLUXNode
        if "prompt" in nimflux_node["inputs"]:
            nimflux_node["inputs"]["prompt"] = prompt
            logging.info(f"Updated prompt to: {prompt}")
        else:
            logging.error("NIMFLUXNode missing prompt in inputs")
            return None

        # Update the steps in the NIMFLUXNode
        if "steps" in nimflux_node["inputs"]:
            nimflux_node["inputs"]["steps"] = steps
            logging.info(f"Updated steps to: {steps}")
        else:
            logging.error("NIMFLUXNode missing steps in inputs")
            return None

        # Update the image filename in the LoadImage node
        if "image" in loadimage_node["inputs"]:
            loadimage_node["inputs"]["image"] = image_name
            logging.info(f"Updated image filename to: {image_name}")
        else:
            logging.error("LoadImage node missing image in inputs")
            return None

        return modified_workflow

    except Exception as e:
        logging.error(f"Error modifying ComfyUI workflow: {e}")
        logging.error(f"Exception type: {type(e).__name__}")
        import traceback

        logging.error(f"Traceback: {traceback.format_exc()}")
        return None


def execute_comfyui_workflow(workflow_data, comfyui_url):
    """
    Executes the ComfyUI workflow via the API and returns the output images.

    Args:
        workflow_data (dict): The workflow data to execute
        comfyui_url (str): The base URL for ComfyUI

    Returns:
        dict: Dictionary of output images by node ID, or None on failure
    """

    try:
        # Generate a unique client ID
        client_id = str(uuid.uuid4())

        # Queue the prompt
        queue_url = f"{comfyui_url}/prompt"
        payload = {"prompt": workflow_data, "client_id": client_id}

        logging.info(f"Queueing ComfyUI workflow at {queue_url}...")

        response = requests.post(
            queue_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )

        response.raise_for_status()
        result = response.json()
        logging.debug(f"ComfyUI API response: {result}")

        prompt_id = result.get("prompt_id")

        if not prompt_id:
            logging.error("No prompt ID returned from ComfyUI")
            logging.error(f"Full response: {result}")
            return None

        logging.info(f"ComfyUI workflow queued with prompt ID: {prompt_id}")

        # Connect to WebSocket to receive execution updates and images
        ws_url = f"ws://{comfyui_url.replace('http://', '').replace('https://', '')}/ws?clientId={client_id}"
        logging.info(f"Connecting to ComfyUI WebSocket: {ws_url}")

        # Check workflow status before connecting to WebSocket
        try:
            status_url = f"{comfyui_url}/prompt"
            status_response = requests.get(status_url, timeout=10)
            if status_response.status_code == 200:
                status_data = status_response.json()
                logging.info(f"ComfyUI status: {status_data}")

                # Check if our prompt is in the queue or executing
                if "queue_running" in status_data:
                    queue_info = status_data["queue_running"]
                    if (
                        queue_info
                        and "prompt_id" in queue_info
                        and queue_info["prompt_id"] == prompt_id
                    ):
                        logging.info("Our workflow is currently executing")
                    else:
                        logging.info("Workflow is in queue or not found")
            else:
                logging.warning(
                    f"Failed to get ComfyUI status: {status_response.status_code}"
                )
        except Exception as e:
            logging.warning(f"Error checking ComfyUI status: {e}")

        ws = websocket.WebSocket()
        ws.settimeout(5)  # 5 second timeout for receive operations
        ws.connect(ws_url)

        output_images = {}
        workflow_completed = False
        start_time = datetime.now()
        timeout_seconds = 300  # 5 minutes timeout

        try:
            while not workflow_completed:
                # Check for timeout
                if (datetime.now() - start_time).total_seconds() > timeout_seconds:
                    logging.warning(
                        "WebSocket timeout reached, stopping workflow monitoring"
                    )
                    break

                try:
                    # Receive message with a reasonable timeout
                    out = ws.recv()
                except websocket.WebSocketTimeoutException:
                    # This is normal - just continue waiting
                    continue
                except Exception as e:
                    logging.warning(f"WebSocket receive error: {e}")
                    continue

                if isinstance(out, str):
                    try:
                        message = json.loads(out)
                        logging.debug(f"Received WebSocket message: {message}")

                        if message["type"] == "executing":
                            data = message["data"]
                            if "prompt_id" in data and data["prompt_id"] == prompt_id:
                                if data["node"] is None:
                                    logging.info("ComfyUI workflow execution completed")
                                    workflow_completed = True
                                    break
                                else:
                                    logging.info(
                                        f"ComfyUI executing node: {data['node']}"
                                    )

                        elif message["type"] == "executed":
                            data = message["data"]
                            if "node" in data:
                                node_id = data["node"]
                                logging.info(f"Node {node_id} completed")

                                # Check if this is the SaveImage node and it has output
                                if (
                                    node_id == "1"
                                    and "output" in data
                                    and "images" in data["output"]
                                ):
                                    logging.info(
                                        f"SaveImage node completed with {len(data['output']['images'])} images"
                                    )
                                    workflow_completed = True
                                    break

                        elif message["type"] == "progress":
                            # Just log progress, don't need to do anything special
                            pass

                    except json.JSONDecodeError as e:
                        logging.warning(f"Failed to parse WebSocket message: {e}")
                        continue

                else:
                    # Binary data - this should be an image
                    logging.info(f"Received binary data of length {len(out)} bytes")

                    # Store the image data
                    if "1" not in output_images:
                        output_images["1"] = []
                    output_images["1"].append(out)
                    logging.info(
                        f"Stored image, total images: {len(output_images['1'])}"
                    )

        finally:
            ws.close()

        # Log what we got from WebSocket
        logging.info(
            f"WebSocket monitoring completed. Workflow completed: {workflow_completed}"
        )
        logging.info(
            f"Images received via WebSocket: {len(output_images.get('1', [])) if '1' in output_images else 0}"
        )

        # If we didn't get images via WebSocket, try the history API as a fallback
        if not output_images:
            logging.info(
                "No images received via WebSocket, checking ComfyUI history..."
            )
            try:
                history_url = f"{comfyui_url}/history"
                response = requests.get(history_url, timeout=30)
                if response.status_code == 200:
                    history = response.json()

                    if prompt_id in history:
                        workflow_history = history[prompt_id]
                        logging.info(f"Found workflow history for prompt {prompt_id}")

                        # Check if SaveImage node has output
                        if "1" in workflow_history.get("outputs", {}):
                            save_image_output = workflow_history["outputs"]["1"]
                            if "images" in save_image_output:
                                images = save_image_output["images"]
                                logging.info(
                                    f"Found {len(images)} images in SaveImage output"
                                )

                                # Download the images
                                if "1" not in output_images:
                                    output_images["1"] = []

                                for i, image_info in enumerate(images):
                                    image_url = f"{comfyui_url}/view?filename={image_info['filename']}&type=output&subfolder={image_info.get('subfolder', '')}"
                                    logging.info(
                                        f"Downloading image {i+1} from: {image_url}"
                                    )

                                    img_response = requests.get(image_url, timeout=30)
                                    if img_response.status_code == 200:
                                        output_images["1"].append(img_response.content)
                                        logging.info(
                                            f"Successfully downloaded image {i+1}"
                                        )
                                    else:
                                        logging.warning(
                                            f"Failed to download image {i+1}: {img_response.status_code}"
                                        )
                else:
                    logging.warning(
                        f"Failed to get ComfyUI history: {response.status_code}"
                    )
            except Exception as e:
                logging.warning(f"Error checking ComfyUI history: {e}")

        if output_images:
            total_images = sum(len(images) for images in output_images.values())
            logging.info(f"Successfully received {total_images} total output images")
            return output_images
        else:
            logging.warning("No output images received from ComfyUI workflow")
            return None

    except websocket.WebSocketException as e:
        logging.error(f"WebSocket error during ComfyUI workflow execution: {e}")
        return None
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Could not connect to the ComfyUI server at {comfyui_url}")
        logging.error(f"Details: {e}")
        return None
    except requests.exceptions.Timeout:
        logging.error("The request to the ComfyUI server timed out after 60 seconds")
        return None
    except requests.exceptions.HTTPError as e:
        logging.error(
            f"ComfyUI API request failed with status code {e.response.status_code}"
        )
        logging.error(f"URL: {e.request.url}")
        try:
            error_json = e.response.json()
            logging.error(f"Error Response: {json.dumps(error_json, indent=2)}")
        except json.JSONDecodeError:
            logging.error(f"Error Response: {e.response.text}")
        return None
    except Exception as e:
        logging.error(
            f"An unexpected error occurred during ComfyUI workflow execution: {e}"
        )
        return None


def comfyui_status(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `comfyui_status` function

    Checks the status of the ComfyUI service by calling the root endpoint.

    Args:
        params: Function parameters (not used)
        context: Context information (not used)
        system_info: System information (not used)

    Returns:
        The function return value with ComfyUI status information
    """
    logging.info("Executing comfyui_status")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        global COMFYUI_URL

        if not COMFYUI_URL:
            return generate_failure_response(
                "COMFYUI_URL not configured. Please set COMFYUI_URL in config.json"
            )

        # Try to hit the root endpoint to check if ComfyUI is responding
        logging.info(f"Checking ComfyUI status at: {COMFYUI_URL}")

        # Make a simple GET request to the root endpoint
        response = requests.get(COMFYUI_URL, timeout=10)
        response.raise_for_status()

        # If we get here, ComfyUI is responding
        logging.info("ComfyUI service is responding")

        # Get system stats for detailed information
        system_stats_url = f"{COMFYUI_URL}/system_stats"
        stats_response = requests.get(system_stats_url, timeout=5)
        stats_response.raise_for_status()
        
        stats_data = stats_response.json()
        system_info = stats_data.get("system", {})
        devices = stats_data.get("devices", [])

        # Build status message
        message_parts = ["ComfyUI service is running and responding."]
        
        # Add ComfyUI version
        if "comfyui_version" in system_info:
            message_parts.append(f"Version: {system_info['comfyui_version']}")
        
        # Add Python version
        if "python_version" in system_info:
            python_version = system_info["python_version"].split()[0]  # Just get version number
            message_parts.append(f"Python: {python_version}")
        
        # Add PyTorch version
        if "pytorch_version" in system_info:
            pytorch_version = system_info["pytorch_version"].split("+")[0]  # Remove CUDA suffix
            message_parts.append(f"PyTorch: {pytorch_version}")
        
        # Add RAM information
        if "ram_total" in system_info and "ram_free" in system_info:
            ram_total = system_info["ram_total"]
            ram_free = system_info["ram_free"]
            ram_used = ram_total - ram_free
            
            ram_total_gb = ram_total / (1024**3)
            ram_used_gb = ram_used / (1024**3)
            ram_free_gb = ram_free / (1024**3)
            
            message_parts.append(f"RAM: {ram_used_gb:.1f} / {ram_total_gb:.1f} GB (Free: {ram_free_gb:.1f} GB)")
        
        # Add VRAM information from first device
        if devices and len(devices) > 0:
            device = devices[0]
            
            if "vram_total" in device and "vram_free" in device:
                vram_total = device["vram_total"]
                vram_free = device["vram_free"]
                vram_used = vram_total - vram_free
                
                vram_total_gb = vram_total / (1024**3)
                vram_used_gb = vram_used / (1024**3)
                vram_free_gb = vram_free / (1024**3)
                
                message_parts.append(f"VRAM: {vram_used_gb:.1f} / {vram_total_gb:.1f} GB (Free: {vram_free_gb:.1f} GB)")
            
            # Add device name
            if "name" in device:
                device_name = device["name"].split(":")[1].strip() if ":" in device["name"] else device["name"]
                message_parts.append(f"GPU: {device_name}")

        return generate_success_response("\n".join(message_parts))

    except requests.exceptions.ConnectionError:
        error_msg = f"Could not connect to ComfyUI server at {COMFYUI_URL}. Is the service running?"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.Timeout:
        error_msg = "Request to ComfyUI server timed out"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.HTTPError as e:
        error_msg = f"ComfyUI request failed with status code {e.response.status_code}"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error checking ComfyUI status: {str(e)}"
        logging.error(error_msg)
        return generate_failure_response(error_msg)


def comfyui_free_memory(
    params: dict = None, context: dict = None, system_info: dict = None
) -> dict:
    """Command handler for `comfyui_free_memory` function

    Calls the ComfyUI /free endpoint to free memory and/or unload models.

    Args:
        params: Function parameters (can include 'unload_models' and 'free_memory')
        context: Context information (not used)
        system_info: System information (not used)

    Returns:
        The function return value indicating success or failure
    """
    logging.info("Executing comfyui_free_memory")

    try:
        # Reload configuration to ensure we have the latest values
        load_config()

        global COMFYUI_URL

        if not COMFYUI_URL:
            return generate_failure_response(
                "COMFYUI_URL not configured. Please set COMFYUI_URL in config.json"
            )

        # Get parameters from params (optional)
        unload_models = params.get("unload_models", True) if params else True
        free_memory = params.get("free_memory", True) if params else True

        # Convert string parameters to boolean if needed
        if isinstance(unload_models, str):
            unload_models = unload_models.lower() in ["true", "1", "yes", "on"]
        if isinstance(free_memory, str):
            free_memory = free_memory.lower() in ["true", "1", "yes", "on"]

        # Construct the free endpoint URL
        free_url = f"{COMFYUI_URL}/free"

        # Prepare the payload
        payload = {"unload_models": unload_models, "free_memory": free_memory}

        logging.info(f"Calling ComfyUI free endpoint at: {free_url}")
        logging.info(f"Payload: {payload}")

        # Make the POST request to the free endpoint
        response = requests.post(
            free_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )

        # Check for HTTP errors
        response.raise_for_status()

        logging.info("Successfully called ComfyUI free endpoint")

        # Create a descriptive message based on what was requested
        actions = []
        if unload_models:
            actions.append("unload models")
        if free_memory:
            actions.append("free memory")

        if actions:
            action_text = " and ".join(actions)
            message = f"ComfyUI has been instructed to {action_text} successfully"
        else:
            message = "ComfyUI free endpoint called successfully (no actions requested)"

        return generate_success_response(message)

    except requests.exceptions.ConnectionError:
        error_msg = f"Could not connect to ComfyUI server at {COMFYUI_URL}. Is the service running?"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.Timeout:
        error_msg = "Request to ComfyUI server timed out after 30 seconds"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except requests.exceptions.HTTPError as e:
        error_msg = f"ComfyUI free endpoint request failed with status code {e.response.status_code}"
        logging.error(error_msg)
        return generate_failure_response(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error calling ComfyUI free endpoint: {str(e)}"
        logging.error(error_msg)
        return generate_failure_response(error_msg)


if __name__ == "__main__":
    main()
