// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    initSidebar();
    initToggleSidebar();
});

// 初始化侧边栏交互
function initSidebar() {
    const sidebarItems = document.querySelectorAll('.sidebar-item');
    
    sidebarItems.forEach(item => {
        item.addEventListener('click', function() {
            // 移除所有active类
            sidebarItems.forEach(i => i.classList.remove('active'));
            
            // 添加active类到当前点击项
            this.classList.add('active');
            
            // 获取对应的面板ID
            const panelId = this.getAttribute('data-panel');
            
            // 隐藏所有面板
            const panels = document.querySelectorAll('.content-panel');
            panels.forEach(panel => panel.classList.remove('active'));
            
            // 显示对应面板
            const targetPanel = document.getElementById(`${panelId}-panel`);
            if (targetPanel) {
                targetPanel.classList.add('active');
            }
        });
    });
}

// 上边栏菜单按钮点击事件
const menuIcon = document.querySelector('.top-bar-icon');
if (menuIcon) {
    menuIcon.addEventListener('click', function() {
        toggleSidebar();
    });
}

// 初始化侧边栏收起/展开功能
function initToggleSidebar() {
    // 从localStorage读取侧边栏状态
    const sidebar = document.querySelector('.sidebar');
    const isCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
    
    if (isCollapsed) {
        sidebar.classList.add('collapsed');
    }
}

// 切换侧边栏收起/展开
function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    sidebar.classList.toggle('collapsed');
    
    // 保存状态到localStorage
    const isCollapsed = sidebar.classList.contains('collapsed');
    localStorage.setItem('sidebarCollapsed', isCollapsed);
}

// 页面 DOM 加载完毕后立即执行
document.addEventListener('DOMContentLoaded', async () => {
    await checkLocalProjects();
});

async function checkLocalProjects() {
    try{
        const response = await fetch('/api/get_projects');
        const data = await response.json();

        const projects = data.projects;
        if (projects.length === 0){
            const div = document.getElementById('works-select-container');
            div.style.display = 'None';
            console.log("0");
        }
    }
    catch(error){
        console.log(error);
    }
}
