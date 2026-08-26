import shutil
import time
from pathlib import Path

import requests

from ...infra.config import infra_config
from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import PROJECT_ROOT, logger, step_log


@step_log("parse_pdf_to_markdown")
def parse_pdf_to_markdown(state: LoadState) -> LoadState:
    # 1.校验路径参数
    pdf_path_obj, local_dir_obj = _validate_path(state)
    # 2.请求MinerU解析PDF文件
    zip_download_url = _upload_pdf_and_poll(pdf_path_obj)
    # 3.下载ZIP并解压获取Markdown文件
    ma_path_obj = _download_and_extract_markdown(
        zip_download_url, local_dir_obj, pdf_path_obj.stem
    )
    # 4.更新state
    state["md_path"] = str(ma_path_obj)
    return state


def _validate_path(state: LoadState) -> tuple[Path, Path]:
    """路径参数校验"""
    pdf_path, local_dir = state.get("pdf_path", ""), state.get("local_dir", "")
    if not pdf_path.strip():
        logger.error(f"pdf_path 参数为空: {pdf_path}")
        raise ValueError(f"pdf_path 参数为空: {pdf_path}")

    if not local_dir.strip():
        local_dir = PROJECT_ROOT / "output"
        logger.warning(f"local_dir 设置为默认目录: {local_dir!s}")

    pdf_path_obj = Path(pdf_path)
    local_dir_obj = Path(local_dir)

    if not pdf_path_obj.is_file():
        logger.error(f"文件不存在/不是文件: {pdf_path_obj}")
        raise FileNotFoundError(f"文件不存在/不是文件: {pdf_path_obj}")
    if not local_dir_obj.is_dir():
        local_dir_obj.mkdir(parents=True, exist_ok=True)
        logger.info(f"local_dir 默认目录创建成功: {local_dir_obj}")
    return pdf_path_obj, local_dir_obj


def _upload_pdf_and_poll(pdf_path_obj: Path) -> str:
    """上传PDF文件至MinerU解析, 并轮询获取返回结果"""
    url = f"{infra_config.mineru_config.base_url}/file-urls/batch"
    header = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {infra_config.mineru_config.api_key}",
    }
    data = {
        "files": [{"name": pdf_path_obj.name, "data_id": pdf_path_obj.stem}],
        "model_version": infra_config.mineru_config.model_vision,
    }

    # 1.请求MinerU服务器 -> 创建可提供解析服务的URL
    response = requests.post(
        url,
        headers=header,
        json=data,
        timeout=infra_config.mineru_config.download_timeout_seconds,
    )
    if response.status_code != 200:
        logger.error(f"请求MinerU服务失败: {response.status_code}")
        raise RuntimeError(f"请求MinerU服务失败: {response.status_code}")

    result = response.json()

    if result.get("code", -1) != 0:
        logger.error(f"请求MinerU服务失败: {result.get('msg', 'Unknown')}")
        raise RuntimeError(f"请求MinerU服务失败: {result.get('msg', 'Unknown')}")

    # 获取 上传文件的URL 和 批次ID
    upload_file_urls = result.get("data", {}).get("file_urls", [])
    batch_id = result.get("data", {}).get("batch_id", "")
    if not upload_file_urls or not batch_id:
        logger.error("请求MinerU服务失败: 未返回文件URL或批次ID")
        raise RuntimeError("请求MinerU服务失败: 未返回文件URL或批次ID")

    upload_file_url = upload_file_urls[0]
    logger.info(
        f"请求MinerU服务器, 创建可提供解析服务的URL成功: \n"
        f"解析路由: {upload_file_url}, \n"
        f"批次_id: {batch_id}"
    )

    # 2.正式上传文件
    # request.put 会携带当前环境到请求头中
    # 该文件上传地址及其敏感, 一旦请求头有多余数据, 立即报错
    # 所以使用 requests.Session() 自建相互隔离的独立环境, 避免请求头被污染
    with requests.Session() as session:
        session.trust_env = False  # 核心
        upload_response = session.put(
            upload_file_url,
            data=pdf_path_obj.read_bytes(),
            timeout=infra_config.mineru_config.download_timeout_seconds,
        )
        if upload_response.status_code != 200:
            logger.error(f"上传PDF文件至解析路由失败: {upload_response.status_code}")
            raise RuntimeError(
                f"上传PDF文件至解析路由失败: {upload_response.status_code}"
            )

        logger.info(f"成功上传PDF文件 {pdf_path_obj.name} 至解析路由 {upload_file_url}")

    # 3.轮询查询解析结果
    max_poll_time = infra_config.mineru_config.poll_timeout_seconds
    poll_time = infra_config.mineru_config.poll_interval_seconds
    start_time = time.time()

    # 是否超时
    while time.time() - start_time < max_poll_time:
        poll_url = (
            f"{infra_config.mineru_config.base_url}/extract-results/batch/{batch_id}"
        )
        # 轮询过程中网络波动报错不raise, 仅记录日志并继续轮询
        try:
            poll_response = requests.get(poll_url, headers=header)
        except requests.exceptions.RequestException as e:
            logger.error(f"轮询请求MinerU服务失败: {e!s}")
            time.sleep(poll_time)
            continue

        poll_status_code = poll_response.status_code

        if poll_status_code != 200:
            if poll_status_code >= 500:
                logger.error(f"轮询请求MinerU服务失败: {poll_response.status_code}")
                time.sleep(poll_time)
                continue
            else:
                logger.warning(f"轮询请求MinerU服务失败: {poll_response.status_code}")
                raise RuntimeError(
                    f"轮询请求MinerU服务失败: {poll_response.status_code}"
                )

        poll_result = poll_response.json()

        if poll_result.get("code", -1) != 0:
            logger.error(f"轮询解析失败: {poll_result.get('msg', 'Unknown')}")
            raise RuntimeError(f"轮询解析失败: {poll_result.get('msg', 'Unknown')}")

        poll_extract = poll_result.get("data", {}).get("extract_result", [])
        extract = poll_extract[0] if len(poll_extract) > 0 else {}
        state = extract.get("state", "")

        if state == "done":
            full_zip_url = extract.get("full_zip_url", "")
            logger.info(f"解析任务 {batch_id} 完成, 下载URL: {full_zip_url}")
            return full_zip_url
        elif state == "failed":
            logger.error(f"解析任务失败: {batch_id}")
            raise RuntimeError(f"解析任务失败: {batch_id}")
        else:
            logger.warning(
                f"解析任务 {batch_id} 运行中, 等待 {poll_time} 秒后继续轮询: {state}"
            )
            time.sleep(poll_time)


def _download_and_extract_markdown(
    zip_download_url: str, local_dir_obj: Path, file_title: str
) -> Path:
    """下载ZIP并解压获取Markdown文件"""
    response = requests.get(
        zip_download_url,
        stream=True,
        timeout=infra_config.mineru_config.download_timeout_seconds,
    )
    if response.status_code != 200:
        logger.error(f"下载ZIP文件失败: {response.status_code}")
        raise RuntimeError(f"下载ZIP文件失败: {response.status_code}")

    # output/zip/
    zip_dir_obj = local_dir_obj / "zip"
    if not zip_dir_obj.is_dir():
        zip_dir_obj.mkdir(parents=True, exist_ok=True)

    # output/zip/[NAME].zip
    zip_file_path_obj = zip_dir_obj / f"{file_title}.zip"
    zip_file_path_obj.write_bytes(response.content)
    logger.info(f"成功下载ZIP文件 {zip_file_path_obj}")

    unzip_file_path_obj = local_dir_obj / file_title
    if unzip_file_path_obj.is_dir():
        shutil.rmtree(unzip_file_path_obj)

    # output/[NAME]/ (解压时自动创建目录)
    shutil.unpack_archive(zip_file_path_obj, extract_dir=unzip_file_path_obj)

    # 遍历目录下所有Markdown文件
    md_file_obj_list = list(unzip_file_path_obj.rglob("*.md"))
    if len(md_file_obj_list) == 0:
        logger.error(f"{unzip_file_path_obj} 未找到任何Markdown文件")
        raise FileNotFoundError(f"{unzip_file_path_obj} 未找到任何Markdown文件")

    # 找到并重命名 full.md 为 [NAME].md
    for md_file_obj in md_file_obj_list:
        if md_file_obj.stem in [file_title, "full"]:
            logger.info(f"{unzip_file_path_obj} 存在 {md_file_obj.name}")
            if md_file_obj.stem == "full":
                md_file_obj = md_file_obj.rename(
                    md_file_obj.with_name(f"{file_title}.md")
                )
                logger.info(f"重命名 {md_file_obj.name} 为 {file_title}.md")
            return md_file_obj

    # 未找到
    logger.error(f"{unzip_file_path_obj} 未找到 {md_file_obj.name}")
    raise FileNotFoundError(f"{unzip_file_path_obj} 未找到 {md_file_obj.name}")
