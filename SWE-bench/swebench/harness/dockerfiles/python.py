_DOCKERFILE_BASE_PY = r"""
FROM --platform={platform} ubuntu:{ubuntu_version}

ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

RUN apt update && apt install -y \
wget \
git \
build-essential \
libffi-dev \
libtiff-dev \
python3 \
python3-pip \
python-is-python3 \
jq \
curl \
locales \
locales-all \
tzdata \
&& rm -rf /var/lib/apt/lists/*

# Download and install conda
RUN wget 'https://repo.anaconda.com/miniconda/Miniconda3-{conda_version}-Linux-{conda_arch}.sh' -O miniconda.sh \
    && bash miniconda.sh -b -p /opt/miniconda3
# Add conda to PATH
ENV PATH=/opt/miniconda3/bin:$PATH
# Add conda to shell startup scripts like .bashrc (DO NOT REMOVE THIS)
RUN conda init --all
RUN conda config --append channels conda-forge

RUN adduser --disabled-password --gecos 'dog' nonroot
"""

_DOCKERFILE_ENV_PY = r"""FROM --platform={platform} {base_image_key}

COPY ./setup_env.sh /path/to/root/
RUN sed -i -e 's/\r$//' /path/to/root/setup_env.sh
RUN chmod +x /path/to/root/setup_env.sh
RUN /bin/bash -c "source ~/.bashrc && /path/to/root/setup_env.sh"

WORKDIR /testbed/

# Automatically activate the testbed environment
RUN echo "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed" > /path/to/root/.bashrc
"""

_DOCKERFILE_INSTANCE_PY = r"""FROM --platform={platform} {env_image_name}

COPY ./setup_repo.sh /path/to/root/
RUN sed -i -e 's/\r$//' /path/to/root/setup_repo.sh
RUN /bin/bash /path/to/root/setup_repo.sh

WORKDIR /testbed/
"""
