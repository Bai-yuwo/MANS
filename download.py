from modelscope.hub.file_download import model_file_download
model_file_download(
    'BAAI/bge-m3',
    file_path='pytorch_model.bin',
    cache_dir='D:/models'
)