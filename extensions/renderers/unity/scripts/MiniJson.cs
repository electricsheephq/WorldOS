using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

/// <summary>
/// Minimal JSON parser (object -> Dictionary&lt;string,object&gt;, array -> List&lt;object&gt;,
/// number -> double, string/bool/null as expected). Public-domain-style, trimmed for the
/// closed-loop fixture loader. Parse only (no serialize). Editor-only use.
/// </summary>
public static class MiniJson
{
    public static object Parse(string json)
    {
        if (string.IsNullOrEmpty(json)) return null;
        int i = 0;
        var v = ParseValue(json, ref i);
        return v;
    }

    static object ParseValue(string s, ref int i)
    {
        SkipWs(s, ref i);
        if (i >= s.Length) return null;
        char c = s[i];
        switch (c)
        {
            case '{': return ParseObject(s, ref i);
            case '[': return ParseArray(s, ref i);
            case '"': return ParseString(s, ref i);
            case 't': case 'f': return ParseBool(s, ref i);
            case 'n': i += 4; return null; // null
            default:  return ParseNumber(s, ref i);
        }
    }

    static Dictionary<string, object> ParseObject(string s, ref int i)
    {
        var o = new Dictionary<string, object>();
        i++; // {
        while (true)
        {
            SkipWs(s, ref i);
            if (i >= s.Length) break;
            if (s[i] == '}') { i++; break; }
            if (s[i] == ',') { i++; continue; }
            string key = ParseString(s, ref i);
            SkipWs(s, ref i);
            if (i < s.Length && s[i] == ':') i++;
            object val = ParseValue(s, ref i);
            o[key] = val;
        }
        return o;
    }

    static List<object> ParseArray(string s, ref int i)
    {
        var a = new List<object>();
        i++; // [
        while (true)
        {
            SkipWs(s, ref i);
            if (i >= s.Length) break;
            if (s[i] == ']') { i++; break; }
            if (s[i] == ',') { i++; continue; }
            a.Add(ParseValue(s, ref i));
        }
        return a;
    }

    static string ParseString(string s, ref int i)
    {
        var sb = new StringBuilder();
        i++; // opening "
        while (i < s.Length)
        {
            char c = s[i++];
            if (c == '"') break;
            if (c == '\\' && i < s.Length)
            {
                char e = s[i++];
                switch (e)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (i + 4 <= s.Length)
                        {
                            int code = int.Parse(s.Substring(i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                            sb.Append((char)code);
                            i += 4;
                        }
                        break;
                    default: sb.Append(e); break;
                }
            }
            else sb.Append(c);
        }
        return sb.ToString();
    }

    static object ParseBool(string s, ref int i)
    {
        if (s[i] == 't') { i += 4; return true; }
        i += 5; return false;
    }

    static object ParseNumber(string s, ref int i)
    {
        int start = i;
        while (i < s.Length && (char.IsDigit(s[i]) || s[i] == '-' || s[i] == '+' || s[i] == '.' || s[i] == 'e' || s[i] == 'E'))
            i++;
        string num = s.Substring(start, i - start);
        double d;
        if (double.TryParse(num, NumberStyles.Any, CultureInfo.InvariantCulture, out d)) return d;
        return 0.0;
    }

    static void SkipWs(string s, ref int i)
    {
        while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
    }
}
