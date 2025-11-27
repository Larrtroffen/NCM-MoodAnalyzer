import time
import re
import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
import calplot
import qrcode
from snownlp import SnowNLP
from tqdm import tqdm
import joypy

# pyncm 相关导入
import pyncm
from pyncm import apis
from pyncm.apis import login, track, playlist, user

class NeteaseMoodAnalyzer:
    def __init__(self):
        self.df = pd.DataFrame()
        # 设置绘图风格
        try:
            import seaborn as sns
            sns.set_theme(style="whitegrid")
        except:
            plt.style.use('seaborn-v0_8')
            
        # 设置中文字体
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False
        
        # 创建输出文件夹
        os.makedirs('outputs', exist_ok=True)

    def login(self):
        """适配新版 pyncm 的扫码登录流程"""
        print(">>> 正在初始化登录...")
        
        uuid_info = login.LoginQrcodeUnikey(dtype=1)
        unikey = uuid_info['unikey']
        qr_url = login.GetLoginQRCodeUrl(unikey)
        
        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_url)
        qr.make(fit=True)
        print("\n=== 请使用网易云音乐APP扫描下方二维码登录 ===")
        qr.print_ascii(invert=True) 
        
        print(">>> 等待扫码中...")
        while True:
            try:
                rsp = login.LoginQrcodeCheck(unikey)
                code = rsp['code']
            except Exception as e:
                time.sleep(2)
                continue
            
            if code == 803:
                print(f"\n>>> 扫码成功，正在同步登录状态...")
                login_cookie = rsp.get('cookie')
                if login_cookie:
                    login.LoginViaCookie(login_cookie)
                else:
                    try:
                        status = login.GetCurrentLoginStatus()
                        pyncm.WriteLoginInfo(status)
                    except Exception as e:
                        return False

                session = pyncm.GetCurrentSession()
                if session.logged_in:
                    print(f">>> 登录完成！欢迎用户: {session.nickname} (UID: {session.uid})")
                    return True
                else:
                    return False
            elif code == 801:
                time.sleep(2)
            elif code == 802:
                print(">>> 扫码成功，请在手机上确认登录...", end="\r")
                time.sleep(2)
            elif code == 800:
                print(">>> 二维码已过期，请重新运行脚本。")
                return False
            else:
                time.sleep(2)

    def get_liked_playlist_data(self, limit=None):
        """获取红心歌单数据"""
        session = pyncm.GetCurrentSession()
        if not session.logged_in:
            print(">>> 错误：未登录，无法获取数据。")
            return

        user_id = session.uid
        try:
            playlists = user.GetUserPlaylists(user_id)
        except Exception as e:
            print(f">>> 获取歌单列表失败: {e}")
            return

        if not playlists.get('playlist'):
            return
            
        liked_playlist_id = playlists['playlist'][0]['id']
        playlist_name = playlists['playlist'][0]['name']
        print(f">>> 正在解析歌单: {playlist_name} (ID: {liked_playlist_id})")
        
        pl_detail = playlist.GetPlaylistInfo(liked_playlist_id)
        all_track_ids = pl_detail['playlist']['trackIds']
        
        if limit and limit < len(all_track_ids):
            all_track_ids = all_track_ids[:limit]
        
        target_ids = [str(t['id']) for t in all_track_ids]
        song_info_map = {} 
        
        batch_size = 500
        print(f">>> 正在批量获取歌曲详情...")
        for i in range(0, len(target_ids), batch_size):
            batch = target_ids[i : i + batch_size]
            try:
                details_resp = track.GetTrackDetail(batch)
                if 'songs' in details_resp:
                    for song in details_resp['songs']:
                        song_info_map[str(song['id'])] = {
                            'name': song['name'],
                            'artist': song['ar'][0]['name'] if song['ar'] else "Unknown"
                        }
                time.sleep(0.5)
            except:
                pass

        data_list = []
        print(f">>> 正在抓取歌词...")
        
        for track_item in tqdm(all_track_ids, unit="song"):
            t_id = str(track_item['id'])
            add_time = track_item['at']
            
            song_name = "Unknown"
            artist_name = "Unknown"

            if t_id in song_info_map:
                info = song_info_map[t_id]
                song_name = info['name']
                artist_name = info['artist']

            lyrics = ""
            try:
                lrc_data = track.GetTrackLyrics(t_id)
                if lrc_data and 'lrc' in lrc_data and 'lyric' in lrc_data['lrc']:
                    lyrics = lrc_data['lrc']['lyric']
                elif lrc_data and 'nolyric' in lrc_data and lrc_data['nolyric']:
                    lyrics = "" 
                else:
                    try:
                        lrc_data_v1 = track.GetTrackLyricsV1(t_id)
                        if 'lrc' in lrc_data_v1 and 'lyric' in lrc_data_v1['lrc']:
                            lyrics = lrc_data_v1['lrc']['lyric']
                    except:
                        pass

                data_list.append({
                    'id': t_id,
                    'name': song_name,
                    'artist': artist_name,
                    'add_time': add_time,
                    'lyrics': lyrics
                })
                time.sleep(0.1) 
            except:
                continue

        self.df = pd.DataFrame(data_list)
        
        # [修改点] 爬取完成后立即保存原始数据
        raw_filename = 'music_data_raw.csv'
        self.df.to_csv(raw_filename, index=False, encoding='utf-8-sig')
        print(f">>> 原始数据已保存至 {raw_filename}，共 {len(self.df)} 首歌曲。")

    def load_from_csv(self, filename='music_mood_data.csv'):
        """从本地 CSV 文件加载数据"""
        # 优先尝试加载 raw 数据，如果没有则加载 mood 数据
        target_file = filename
        if not os.path.exists(target_file):
            if os.path.exists('music_data_raw.csv'):
                target_file = 'music_data_raw.csv'
                print(f">>> 未找到 {filename}，将加载原始数据 {target_file}")
            else:
                print(f">>> 错误：找不到数据文件。")
                return False
        
        print(f">>> 正在从本地文件 {target_file} 加载数据...")
        try:
            self.df = pd.read_csv(target_file)
            # 恢复时间格式
            if 'date' in self.df.columns:
                self.df['date'] = pd.to_datetime(self.df['date'])
            elif 'add_time' in self.df.columns:
                self.df['date'] = pd.to_datetime(self.df['add_time'], unit='ms')
            
            print(f">>> 加载成功，共 {len(self.df)} 条数据。")
            return True
        except Exception as e:
            print(f">>> 加载文件失败: {e}")
            return False

    def clean_lyrics(self, text):
        """清洗歌词"""
        if not isinstance(text, str) or not text:
            return ""
        text = re.sub(r'\[.*?\]', '', text)
        lines = text.split('\n')
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if any(x in line for x in ['作词', '作曲', '编曲', '制作', '混音', '录音', '母带']):
                continue
            cleaned_lines.append(line)
        return " ".join(cleaned_lines)

    def analyze_sentiment(self):
        """情感分析（包含纯音乐检测与本地音频模型占位）"""
        if self.df.empty:
            print(">>> 没有数据可分析")
            return
        
        print(">>> 正在进行情感分析...")
        sentiments = []
        
        # 确保有 date 列
        if 'date' not in self.df.columns:
            self.df['date'] = pd.to_datetime(self.df['add_time'], unit='ms')
        
        # 模拟本地音乐库路径（用于占位符逻辑）
        local_music_dir = "local_music" 
        
        for index, row in tqdm(self.df.iterrows(), total=self.df.shape[0]):
            lrc = str(row['lyrics']) if pd.notna(row['lyrics']) else ""
            song_name = str(row['name'])
            
            # [修改点] 逻辑1：纯音乐检测
            if "纯音乐，请欣赏" in lrc:
                sentiments.append(0.5) # 中立
                continue
            
            # [修改点] 逻辑2：本地音频模型占位符
            # 检查是否存在同名文件（假设为mp3）
            local_file_path = os.path.join(local_music_dir, f"{song_name}.mp3")
            if os.path.exists(local_file_path):
                # 这里是占位符，不实际执行耗时的音频分析
                # print(f"检测到本地文件 {song_name}，调用音频情感模型...", end="\r")
                # audio_score = AudioModel.predict(local_file_path)
                pass 

            # 常规文本分析
            clean_lrc = self.clean_lyrics(lrc)
            if not clean_lrc or len(clean_lrc) < 5:
                sentiments.append(0.5)
                continue
            try:
                s = SnowNLP(clean_lrc)
                sentiments.append(s.sentiments)
            except:
                sentiments.append(0.5)
        
        self.df['sentiment'] = sentiments
        self.df = self.df.sort_values('date')
        
        # [修改点] 分析完成后保存处理后的数据
        output_csv = 'music_mood_data.csv'
        self.df.to_csv(output_csv, index=False, encoding='utf-8-sig')
        print(f">>> 分析完成，结果已保存至 {output_csv}")

    def visualize(self):
        """
        可视化：生成多种优化后的图表并保存到 outputs 文件夹。
        针对数据稀疏和密集并存的特点进行了特别优化。
        """
        if self.df.empty or 'sentiment' not in self.df.columns:
            print(">>> 数据为空或未包含情感分析结果，无法绘图")
            return

        print(">>> 正在生成优化版图表并保存至 outputs 文件夹...")
        
        # 准备数据，确保索引是 datetime 类型
        df_viz = self.df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df_viz['date']):
            df_viz['date'] = pd.to_datetime(df_viz['date'])
        df_viz = df_viz.set_index('date').sort_index()

        # --- 1. 情感趋势图 (优化版) ---
        plt.figure(figsize=(16, 8))
        monthly_mood = df_viz['sentiment'].resample('ME').mean()
        rolling_mood = monthly_mood.rolling(window=12, center=True, min_periods=3).mean()
        interpolated_mood = rolling_mood.interpolate(method='linear')
        plt.scatter(monthly_mood.index, monthly_mood, 
                    alpha=0.4, s=25, color='gray', label='月度平均情感')
        plt.plot(interpolated_mood.index, interpolated_mood, 
                color='#e53935', linewidth=3, label='年度平滑趋势 (12个月移动平均)')
        plt.xlim(df_viz.index.min() - pd.Timedelta(days=14), df_viz.index.max() + pd.Timedelta(days=14))
        plt.ylim(-0.05, 1.05)
        plt.title('听歌情感倾向平滑趋势', fontsize=18, pad=20)
        plt.ylabel('情感分数 (0=悲伤, 1=快乐)', fontsize=12)
        plt.xlabel('时间', fontsize=12)
        plt.legend(loc='upper left')
        plt.grid(True, which='major', linestyle='--', linewidth=0.5)
        plt.tight_layout()
        plt.savefig(os.path.join('outputs', '1_smooth_trend.png'), dpi=300)
        plt.close()

        # --- 2. 年度情感分布小提琴图 (优化版) ---
        plt.figure(figsize=(14, 7))
        df_plot_yearly = df_viz.copy()
        df_plot_yearly['year'] = df_plot_yearly.index.year
        sns.violinplot(x='year', y='sentiment', data=df_plot_yearly, 
                    palette='viridis', inner='quartile', linewidth=1.5)
        sns.stripplot(x='year', y='sentiment', data=df_plot_yearly, 
                    jitter=True, size=2, color='black', alpha=0.2)
        plt.title('不同年份的听歌情感分布 (小提琴图)', fontsize=18, pad=20)
        plt.ylabel('情感分数', fontsize=12)
        plt.xlabel('年份', fontsize=12)
        plt.tight_layout()
        plt.savefig(os.path.join('outputs', '2_yearly_violin_distribution.png'), dpi=300)
        plt.close()

        # --- 3. 时间-情感二维密度图 (优化版) ---
        plt.figure(figsize=(16, 8))
        sns.kdeplot(data=df_viz, x=df_viz.index, y='sentiment', 
                    fill=True, cmap="magma", levels=20, thresh=0.01)
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        plt.gcf().autofmt_xdate()
        plt.xlim(df_viz.index.min(), df_viz.index.max())
        plt.ylim(-0.05, 1.05)
        plt.title('听歌时间与情感密度图', fontsize=18, pad=20)
        plt.ylabel('情感分数', fontsize=12)
        plt.xlabel('时间', fontsize=12)
        plt.tight_layout()
        plt.savefig(os.path.join('outputs', '3_sentiment_2d_density.png'), dpi=300)
        plt.close()

        # --- 4. 整体情感分布直方图 (保留并优化) ---
        plt.figure(figsize=(10, 6))
        sns.histplot(df_viz['sentiment'], bins=50, kde=True, color='dodgerblue', alpha=0.6)
        plt.axvline(df_viz['sentiment'].mean(), color='red', linestyle='--', label=f'平均值: {df_viz["sentiment"].mean():.2f}')
        plt.axvline(0.5, color='gray', linestyle=':', label='中立线')
        plt.title('整体听歌情感分布', fontsize=18, pad=20)
        plt.xlabel('情感分数 (左=悲伤, 右=快乐)', fontsize=12)
        plt.ylabel('歌曲数量', fontsize=12)
        plt.legend()
        plt.tight_layout()
        plt.savefig(os.path.join('outputs', '4_sentiment_distribution.png'), dpi=300)
        plt.close()

# --- 5. 每日情感日历热力图 (滑动平均平滑版) ---
        if calplot:
            try:
                # --- 1. 数据预处理：计算滑动平均 ---
                
                # 步骤 1.1: 先按天聚合，计算每日的原始平均情感
                # 如果某天没有数据，其值将是 NaN
                daily_events = df_viz['sentiment'].resample('D').mean()

                # 步骤 1.2: 应用滑动窗口计算平滑后的情感趋势
                # window=14: 使用过去14天的数据来平滑当天
                # min_periods=1: 即使窗口内只有1天数据，也计算均值，保证数据开头不为空
                smoothed_events = daily_events.rolling(window=30, min_periods=1).mean()
                
                # --- 2. 绘图 ---
                
                # 使用 calplot.calplot() 函数绘制平滑后的数据
                fig, ax = calplot.calplot(
                    smoothed_events,          # 使用平滑后的数据
                    cmap='coolwarm',          # 蓝色 (悲伤) -> 白色 (中性) -> 红色 (快乐)
                    figsize=(16, 10),         # 调整图像大小
                    suptitle='听歌情感趋势日历图 (30日滑动平均)',
                    suptitle_kws={'fontsize': 22, 'y': 1.05},
                    # 精细控制颜色范围，确保 0.5 是中性色
                    vmin=0.0,                 # 情感分数的最小值
                    vmax=1.0                  # 情感分数的最大值
                )

                # 保存图像
                fig.savefig(os.path.join('outputs', '5_calendar_heatmap_smoothed.png'), dpi=300, bbox_inches='tight')
                plt.close(fig) 
                
            except Exception as e:
                print(f">>> 生成平滑日历图时出错: {e}")
                print(">>> 请确保您的数据中包含有效的时间和情感值。")

            print(">>> 所有图表已生成完毕，请查看 outputs 文件夹。")


if __name__ == "__main__":
    analyzer = NeteaseMoodAnalyzer()
    
    # ================= 设置 =================
    # True: 本地模式 (加载数据 -> 分析 -> 绘图)
    # False: 在线模式 (扫码 -> 爬取 -> 保存 -> 分析 -> 保存 -> 绘图)
    USE_LOCAL_DATA = True
    # =======================================

    if USE_LOCAL_DATA:
        # 本地模式逻辑：加载 -> 强制重新分析(应用新逻辑) -> 可视化
        if analyzer.load_from_csv('music_mood_data.csv'):
            analyzer.analyze_sentiment()
            analyzer.visualize()
    else:
        # 在线模式
        if analyzer.login():
            analyzer.get_liked_playlist_data(limit=None) 
            analyzer.analyze_sentiment()
            analyzer.visualize()
