import sqlparse
from sqlparse.sql import IdentifierList, TokenList, Where
import sqlparse.tokens as Tokens


class SqlParser:
    supported_keywords = ['SELECT', 'FROM', 'WHERE', 'GROUP',
                          'HAVING', 'ORDER', 'UNION', 'LIMIT']

    def __init__(self, sql):
        self.tokens = sqlparse.parse(sql)[0].tokens
        self.keywords = get_offsets(self.tokens, self.supported_keywords)

    @property
    def fields(self):
        for token in self.tokens[
                     self.keywords['SELECT'] + 1:self.keywords['FROM']]:
            if isinstance(token, IdentifierList):
                return list(self.parse_columns(token.get_identifiers()))

    @staticmethod
    def parse_columns(tokens):
        for token in tokens:
            offsets = get_offsets(token.tokens, ["AS"])
            if "AS" in offsets:
                yield (
                    extract(token.tokens[:offsets["AS"]]).value,
                    extract(token.tokens[offsets["AS"] + 1:]).value
                )
            else:
                yield (token.value, token.value)


    @property
    def from_(self):
        end_from = min(self.keywords.get(kw, len(self.tokens))
                       for kw in self.supported_keywords[2:])

        return extract(self.tokens[self.keywords['FROM'] + 1:end_from]).value

    @property
    def where(self):
        if 'WHERE' in self.keywords:
            token = self.tokens[self.keywords['WHERE']]
            return extract(token.tokens[1:]).value

    @property
    def sql_without_limit(self):
        if "LIMIT" in self.keywords:
            return extract(self.tokens[:self.keywords["LIMIT"]]).value
        else:
            return extract(self.tokens).value


def get_offsets(tokens, keywords):
    keyword_offset = {}
    for idx, token in enumerate(tokens):
        if isinstance(token, Where) and "WHERE" in keywords:
            keyword_offset["WHERE"] = idx
        if token.match(Tokens.Keyword, keywords) or \
                token.match(Tokens.DML, keywords):
            keyword_offset[token.value.upper()] = idx
    return keyword_offset


def extract(token_list):
    tokens = list(TokenList(token_list).flatten())
    for token in tokens:
        if token.is_whitespace():
            token.value = " "
    return TokenList(tokens)
