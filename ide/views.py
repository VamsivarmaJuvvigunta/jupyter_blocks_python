from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import logging
import jupyter_client
import os
import tempfile
import subprocess
import queue


logger = logging.getLogger(__name__)

class ExecuteCodeView(APIView):
    kernels = {}
    code_sessions = {}

    def post(self, request):
        logger.debug("Received request: %s", request.data)

        code = request.data.get("code")
        language = request.data.get("language")
        block_id = request.data.get("block_id")
        execute_in_order = request.data.get("execute_in_order", False)

        if not code or not language:
            logger.error("Code or language not provided")
            return Response({"error": "Code or language not provided"}, status=status.HTTP_400_BAD_REQUEST)

        execution_results = {}

        if execute_in_order:
            if language not in self.code_sessions:
                self.code_sessions[language] = []

            self.code_sessions[language].append(code)
            full_code = "\n".join(self.code_sessions[language])
            result, error = self.execute_code(language, full_code, block_id)
        else:
            result, error = self.execute_code(language, code, block_id)

        if error:
            logger.error("Error executing code: %s", error)
            return Response({"error": error}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        logger.debug("Code executed successfully, output: %s", result)
        execution_results[block_id] = result
        return Response({"output": result})

    def execute_code(self, language, code, block_id):
        if language in ['cpp', 'java', 'c']:
            return self.execute_compiled_code(language, code, block_id)
        elif language in ['html', 'css']:
            return self.execute_html_css(code)
        else:
            return self.execute_code_with_jupyter(language, code, block_id)

    def execute_html_css(self, code):
        try:
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.html') as tmp_file:
                tmp_file.write(code.encode())
                tmp_file_path = tmp_file.name

            
            if os.name == 'nt':  
                command = f'start {tmp_file_path}'  
            else:
                command = f'xdg-open {tmp_file_path}'  

            subprocess.run(command, shell=True)

            
            return f"HTML/CSS executed successfully. View it at: file://{tmp_file_path}", None

        except Exception as e:
            logger.exception("Exception occurred during HTML/CSS execution: %s", e)
            return None, str(e)

    def execute_code_with_jupyter(self, language, code, block_id):
        km = None
        kc = None
        try:
            kernel_name = self.get_kernel_name(language)
            if not kernel_name:
                logger.error("Unsupported language: %s", language)
                return None, f"Unsupported language: {language}"

            if language not in self.kernels:
                km = jupyter_client.KernelManager(kernel_name=kernel_name)
                km.start_kernel()
                kc = km.client()
                kc.start_channels()
                self.kernels[language] = (km, kc)
            else:
                km, kc = self.kernels[language]

            kc.execute(code)

            try:
                reply = kc.get_shell_msg(timeout=10)
                logger.debug("Kernel replied with: %s", reply)

                if 'content' in reply and 'status' in reply['content']:
                    if reply['content']['status'] == 'ok':
                        result_msgs = []
                        while True:
                            msg = kc.get_iopub_msg(timeout=10)
                            logger.debug("IOPub message received: %s", msg)

                            if msg['msg_type'] == 'execute_result':
                                result_msgs.append(msg['content'].get('data', {}).get('text/plain', ''))
                            elif msg['msg_type'] == 'stream':
                                result_msgs.append(msg['content'].get('text', ''))
                            elif msg['msg_type'] == 'error':
                                return None, '\n'.join(msg['content'].get('traceback', ''))

                            if msg['msg_type'] == 'status' and msg['content']['execution_state'] == 'idle':
                                break

                        result = '\n'.join(result_msgs) or "executed succesfully."
                        logger.debug("Execution result: %s", result)
                        return result, None
                    else:
                        error = reply['content'].get('evalue', 'Unknown error occurred during execution')
                        logger.error("Kernel execution error: %s", error)
                        return None, error
                else:
                    logger.error("Invalid reply from kernel")
                    return None, "Invalid reply from kernel"

            except queue.Empty as timeout_error:
                logger.error("Kernel execution timed out: %s", timeout_error)
                return None, "Execution timed out. The code may be too complex or there could be a kernel issue."

        except Exception as e:
            logger.exception("Exception occurred during code execution: %s", e)
            return None, str(e)
        finally:
            if km:
                logger.debug("Keeping kernel alive for language: %s", language)

    def execute_compiled_code(self, language, code, block_id):
        tmp_file_name = None
        try:
            if language == 'java':
                class_name = code.split()[2]  
                tmp_file_name = f"{class_name}.java"
            else:
                tmp_file_name = "temp_file"

            file_extension = self.get_file_extension(language)

            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp_file:
                tmp_file_path = tmp_file.name
                tmp_file.write(code.encode())
                tmp_file.flush()

            if language == 'java':
                os.rename(tmp_file_path, tmp_file_name)

            command = self.get_execution_command(language, tmp_file_name if language == 'java' else tmp_file_path)
            if not command:
                return None, f"Unsupported language: {language}"

            logger.debug("Executing command: %s", command)
            process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True)

            if process.returncode != 0:
                logger.error("Execution error: %s", process.stderr)
                return None, process.stderr

            logger.debug("Execution successful, output: %s", process.stdout)
            return process.stdout, None

        except Exception as e:
            logger.exception("Exception occurred during execution: %s", e)
            return None, str(e)
        finally:
            if tmp_file_name and os.path.exists(tmp_file_name):
                os.remove(tmp_file_name)
            elif tmp_file_path and os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)

    def get_kernel_name(self, language):
        kernels = {
            'python': 'python3',
            'javascript': 'javascript',
            'r': 'ir',
            'java': 'java',             
            'cpp': 'xeus-cling',        
            'c': 'clang',     
            
        }
        logger.debug("Kernel name for %s: %s", language, kernels.get(language, None))
        return kernels.get(language, None)

    def get_execution_command(self, language, file_path):
        commands = {
            'cpp': f'g++ {file_path} -o {file_path}.out && {file_path}.out',
            'java': f'javac {file_path} && java {file_path.rstrip(".java")}',
            'c': f'gcc {file_path} -o {file_path}.out && {file_path}.out',
        }
        logger.debug("Execution command for %s: %s", language, commands.get(language, None))
        return commands.get(language, None)

    def get_file_extension(self, language):
        extensions = {
            'python': '.py',
            'javascript': '.js',
            'cpp': '.cpp',
            'java': '.java',
            'r': '.R',
            'c': '.c',
        }
        logger.debug("File extension for %s: %s", language, extensions.get(language, ''))
        return extensions.get(language, '')


class ExecuteAllCodeView(APIView):
    def post(self, request):
        logger.debug("Received request to execute all code blocks: %s", request.data)

        
        code_blocks = request.data.get("code_blocks", [])
        language = request.data.get("language")

        
        if not code_blocks or not language:
            logger.error("Code blocks or language not provided")
            return Response({"error": "Code blocks or language not provided"}, status=status.HTTP_400_BAD_REQUEST)

        
        overall_results = {}

        
        for block in code_blocks:
            block_id = block.get("block_id")
            code = block.get("code")

            
            if not code or not block_id:
                logger.warning("Code or block ID missing for block: %s", block)
                continue

            
            result, error = self.execute_code(language, code, block_id)

            
            if error:
                logger.error("Error executing block %s: %s", block_id, error)
                overall_results[block_id] = {"error": error}
            else:
                overall_results[block_id] = {"output": result}

        
        logger.debug("Execution results for all code blocks: %s", overall_results)
        return Response(overall_results)

    def execute_code(self, language, code, block_id):
        
        if language in ['cpp', 'java', 'c']:
            return ExecuteCodeView().execute_compiled_code(language, code, block_id)
        elif language in ['html', 'css']:
            return ExecuteCodeView().execute_html_css(code)
        else:
            return ExecuteCodeView().execute_code_with_jupyter(language, code, block_id)
