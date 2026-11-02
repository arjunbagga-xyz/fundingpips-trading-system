//+------------------------------------------------------------------+
//| mt5_event_relay.mq5                                              |
//| Event relay EA - sends trade events to Python listener via TCP     |
//| Attach to any chart. Monitors ALL symbols/trades.                  |
//+------------------------------------------------------------------+
#property copyright "FundingPips Trading System"
#property version   "1.00"
#property strict

input int    Port              = 9876;
input string Host              = "127.0.0.1";
input int    ReconnectInterval = 5;   // seconds between reconnect attempts
input string FlagFile          = "ea_relay_enabled.flag";

int      socket_handle = INVALID_HANDLE;
bool     connected     = false;
datetime last_reconnect = 0;

//+------------------------------------------------------------------+
int OnInit()
{
    EventSetTimer(1);
    TryConnect();
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    EventKillTimer();
    if (socket_handle != INVALID_HANDLE)
    {
        SocketClose(socket_handle);
        socket_handle = INVALID_HANDLE;
    }
    connected = false;
}

//+------------------------------------------------------------------+
void OnTimer()
{
    if (!connected)
    {
        datetime now = TimeCurrent();
        if (now - last_reconnect >= ReconnectInterval)
        {
            last_reconnect = now;
            TryConnect();
        }
    }
}

//+------------------------------------------------------------------+
void TryConnect()
{
    socket_handle = SocketCreate();
    if (socket_handle == INVALID_HANDLE)
    {
        Print("EA_RELAY: Socket create failed");
        return;
    }
    if (SocketConnect(socket_handle, Host, Port, 1000))
    {
        connected = true;
        Print("EA_RELAY: Connected to Python listener");
    }
    else
    {
        SocketClose(socket_handle);
        socket_handle = INVALID_HANDLE;
        connected = false;
    }
}

//+------------------------------------------------------------------+
void OnTrade()
{
    // Check enable flag
    if (!FileIsExist(FlagFile))
        return;

    if (!connected || socket_handle == INVALID_HANDLE)
        return;

    // Get latest deal (last 24h only for performance)
    HistorySelect(TimeCurrent() - 86400, TimeCurrent());
    int total = HistoryDealsTotal();
    if (total == 0)
        return;

    ulong deal_ticket = HistoryDealGetTicket(total - 1);
    if (deal_ticket == 0)
        return;

    // Build JSON payload
    string symbol   = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
    int    dtype    = (int)HistoryDealGetInteger(deal_ticket, DEAL_TYPE);
    double volume   = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
    double price    = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
    double profit   = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
    double sl       = HistoryDealGetDouble(deal_ticket, DEAL_SL);
    double tp       = HistoryDealGetDouble(deal_ticket, DEAL_TP);
    int    entry    = (int)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
    long   pos_id   = (long)HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
    long   magic    = (long)HistoryDealGetInteger(deal_ticket, DEAL_MAGIC);
    string comment  = HistoryDealGetString(deal_ticket, DEAL_COMMENT);

    string json = "{";
    json += "\"deal_ticket\":" + IntegerToString((long)deal_ticket) + ",";
    json += "\"position\":" + IntegerToString(pos_id) + ",";
    json += "\"symbol\":\"" + symbol + "\",";
    json += "\"type\":" + IntegerToString(dtype) + ",";
    json += "\"volume\":" + DoubleToString(volume, 2) + ",";
    json += "\"price\":" + DoubleToString(price, 5) + ",";
    json += "\"profit\":" + DoubleToString(profit, 2) + ",";
    json += "\"sl\":" + DoubleToString(sl, 5) + ",";
    json += "\"tp\":" + DoubleToString(tp, 5) + ",";
    json += "\"entry\":" + IntegerToString(entry) + ",";
    json += "\"magic\":" + IntegerToString(magic) + ",";
    json += "\"comment\":\"" + comment + "\"";
    json += "}\n";

    // Send
    uchar data[];
    int len = StringToCharArray(json, data, 0, -1, CP_UTF8);
    int sent = SocketSend(socket_handle, data, len - 1);
    if (sent <= 0)
    {
        connected = false;
        Print("EA_RELAY: Send failed, disconnected");
        SocketClose(socket_handle);
        socket_handle = INVALID_HANDLE;
    }
}
//+------------------------------------------------------------------+