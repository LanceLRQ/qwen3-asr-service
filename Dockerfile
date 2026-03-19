FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 代理设置（构建时可选传入，不会写入最终镜像）
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY=localhost,127.0.0.1
ENV http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY} no_proxy=${NO_PROXY}

# 替换 APT 源为华科镜像（加速国内构建）
RUN sed -i 's|http://archive.ubuntu.com/ubuntu|https://mirrors.hust.edu.cn/ubuntu|g' /etc/apt/sources.list && \
    sed -i 's|http://security.ubuntu.com/ubuntu|https://mirrors.hust.edu.cn/ubuntu|g' /etc/apt/sources.list

# 安装系统依赖：Python 3.12、ffmpeg
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y \
        python3.12 \
        python3.12-venv \
        python3.12-dev \
        ffmpeg \
        curl \
    && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12 \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先安装 PyTorch CUDA 版（利用缓存层）
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# 安装项目依赖
COPY asr-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --ignore-installed -r requirements.txt

# 复制应用代码
COPY asr-service/app /app/app

# 创建模型挂载目录
RUN mkdir -p /app/models /app/logs

# 清除构建代理，不带入运行时
ENV http_proxy="" https_proxy="" no_proxy=""

EXPOSE 8765

ENTRYPOINT ["python", "-m", "app.main", "--host", "0.0.0.0"]
