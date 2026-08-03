from __future__ import annotations

# Standard base HTML responsive layout for all outbound transactional emails
BASE_EMAIL_HTML_LAYOUT = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #333333;
            background-color: #f9f9f9;
            margin: 0;
            padding: 0;
        }}
        .container {{
            max-width: 600px;
            margin: 40px auto;
            background: #ffffff;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
            border: 1px solid #e1e1e1;
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .logo {{
            font-size: 24px;
            font-weight: bold;
            color: #1a73e8;
            text-decoration: none;
        }}
        .content {{
            margin-bottom: 30px;
        }}
        .button-container {{
            text-align: center;
            margin: 35px 0;
        }}
        .button {{
            display: inline-block;
            padding: 12px 24px;
            background-color: #1a73e8;
            color: #ffffff !important;
            text-decoration: none;
            border-radius: 4px;
            font-weight: 600;
        }}
        .footer {{
            font-size: 12px;
            color: #666666;
            text-align: center;
            border-top: 1px solid #eeeeee;
            padding-top: 20px;
            margin-top: 30px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <span class="logo">{brand_name}</span>
        </div>
        <div class="content">
            {content_html}
        </div>
        <div class="footer">
            <p>{footer_text}</p>
        </div>
    </div>
</body>
</html>
"""