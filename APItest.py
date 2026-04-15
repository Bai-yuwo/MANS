import requests
import json

url = "https://ark.cn-beijing.volces.com/api/compatible/v1/messages"
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer 90c23f1f-d987-4646-b60c-305047235827"
}
payload = {
    # "model": "doubao-seed-2-0-code-preview-260215",
    "model": "doubao-seed-2-0-code",
    "messages": [{"role": "user", "content": "这是一个api连接测试，请告诉我你的名字。"}],
    "stream": True,
    "max_tokens": 500
}

response = requests.post(url, headers=headers, json=payload, stream=True)

if response.status_code == 200:
    for line in response.iter_lines():
        if line:
            line = line.decode('utf-8')
            if line.startswith('data: '):
                data_str = line[6:]
                if data_str.strip() == '[DONE]':
                    break
                try:
                    data = json.loads(data_str)
                    if data['type'] == 'content_block_delta':
                        print(data['delta']['text'], end='', flush=True) # 这里你可以提取并处理具体的文本内容
                except json.JSONDecodeError:
                    print(f"Error decoding JSON: {data_str}")
else:
    print(f"Request failed with status {response.status_code}: {response.text}")