; Download Googlenet-v1 model from OMZ and convert it into IR model

python "%INTEL_OPENVINO_DIR%\deployment_tools\tools\model_downloader\downloader.py" ^
    --name googlenet-v1
python "%INTEL_OPENVINO_DIR%\deployment_tools\tools\model_downloader\converter.py" ^
    --name googlenet-v1
