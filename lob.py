#!/usr/bin/env python

"""
Limit order book 

Notes
-----
* Market orders are immediately executed at best buy and best ask values; if the
  book is empty (i.e., no limit orders have been placed yet), the market order
  is automatically canceled.
* All outstanding limit orders expire at the end of the day.
* Attempting to execute a buy/sell market order prior to the arrival of any
  sell/buy limit orders that can satisfy it (both in terms of quantity and price).
* When a limit order arrives that can satisfy an outstanding limit order, it is
  executed and the corresponding order is removed from the book.
* More information re LOBs can be found at 
  http://iopscience.iop.org/0295-5075/75/3/510/fulltext/
"""

import copy
import csv
import datetime
import logging
import odict
import pandas
import sys
col_names = \
  ['record_indicator',
   'segment',
   'order_number',
   'trans_date',
   'trans_time',
   'buy_sell_indicator',
   'activity_type',
   'symbol',
   'instrument',
   'expiry_date',
   'strike_price',
   'option_type',
   'volume_disclosed',
   'volume_original',
   'limit_price',
   'trigger_price',
   'mkt_flag',
   'on_stop_flag',
   'io_flag',
   'spread_comb_type',
   'algo_ind',
   'client_id_flag']

# Some aliases for bids and asks:
BID = BUY = 'B'
ASK = SELL = 'S'

class LimitOrderBook(object):
    """
    Limit order book.

    Parameters    
    ----------

    Notes
    -----
    Orders at each price level are stored in an ordered dict keyed by order
    number; new orders are implicitly appended whenever they are added to the dict.

    """
    
    def __init__(self, tick_size=0.05):
        self.logger = logging.getLogger('lob')

        # All limit prices are a multiple of the tick size:
        self.tick_size = tick_size

        # The order data in the book is stored in two dictionaries of ordered
        # dicts; the keys of each dictionary correspond to the price levels of
        # each ordered dict. The ordered dicts are used as
        # queues; adding a new entry with a key corresponding to the order
        # number is equivalent to pushing it into the queue, and the ordered
        # dict permits one to "pop" its first entry:
        self._book_data = {}
        self._book_data[BID] = {}
        self._book_data[ASK] = {}

        self._book_data_hist = {}
        self._book_data_hist[BID] = {}
        self._book_data_hist[ASK] = {}
        
        # Counters used to assign unique identifiers to generated events and
        # trades:
        self._event_counter = 1
        self._trade_counter = 1
        
        # Trades performed as orders arrive are recorded in this dictionary:
        self._trades = odict.odict()

        # Events are stored in this dictionary:
        self._events = odict.odict()
        
        # The best bids and asks are stored in two dictionaries:
        self._best_prices = {}
        self._best_prices[BID] = odict.odict()
        self._best_prices[ASK] = odict.odict()
        
    def clear_book(self):
        """
        Clear all outstanding limit orders from the book

        Notes
        -----
        The trade counter is reset to 1, but all previously
        recorded trades are not erased.
        
        """

        self.logger.info('clearing outstanding limit orders')
        for d in self._book_data.keys():
            self._book_data[d].clear()
        self._trade_counter = 1
        
    def process(self, df):
        """
        Process order data

        Parameters
        ----------
        df : pandas.DataFrame
           Each row of this DataFrame instance contains a single order.

        """

        day = None
        for row in df.iterrows():
            order = row[1].to_dict()
            self.logger.info('processing order: %i' % order['order_number'])

            # Reset the limit order book when a new day of orders begins:
            trans_date = datetime.datetime.strptime(order['trans_date'], '%m/%d/%Y')
            if day is None:
                day = trans_date.day
                self.logger.info('setting day: %s' % day)
            elif day != trans_date.day:
                self.logger.info('new day - book reset')
                self.clear_book()
                
            if order['activity_type'] == 1:
                self.add(order)
            elif order['activity_type'] == 3:
                self.cancel(order)
            elif order['activity_type'] == 4:
                # XXX It seems that a few market orders are listed as modify orders;
                # temporarily treat them as add operations XXX                  
                if order['mkt_flag'] == 'Y':
                    self.add(order)
                else:    
                    self.modify(order)
            else:
                raise ValueError('unrecognized activity type %i' % \
                                 order['activity_type'])

    # def save_best_bid_ask_data(self, date_time):
    #     """
    #     Save the best bid and ask price and total volume.
    #     """

    #     # Save the best bid and ask, along with the associated order volumes
    #     # at the corresponding price levels:
    #     best_bid_price = self.best_bid_price()

    #     if best_bid_price is not None:
    #         od = self.price_level(BID, best_bid_price)
    #         volume_original = \
    #           sum([order['volume_original'] for order in od.itervalues()])
    #         volume_disclosed = \
    #           sum([order['volume_disclosed'] for order in od.itervalues()])
    #     else:
    #         volume_original = volume_disclosed = 0.0
    #     self._best_prices[BID][date_time] = \
    #       (best_bid_price, volume_original, volume_disclosed)

    #     best_ask_price = self.best_ask_price()            
    #     if best_ask_price is not None:
    #         od = self.price_level(ASK, best_ask_price)
    #         volume_original = \
    #           sum([order['volume_original'] for order in od.itervalues()])
    #         volume_disclosed = \
    #           sum([order['volume_disclosed'] for order in od.itervalues()])
    #     else:
    #         volume_original = volume_disclosed = 0.0
    #     self._best_prices[ASK][date_time] = \
    #       (best_ask_price, volume_original, volume_disclosed)

    def save_book_data(self, date_time):
        """
        Save a full copy of the bid and ask parts of the limit order book.

        Notes
        -----
        This can potentially consume memory very rapidly; it should probably be
        replaced with a disk-based mechanism.
        
        """
        self._book_data_hist[BID][date_time] = \
          copy.deepcopy(self._book_data[BID])
        self._book_data_hist[ASK][date_time] = \
          copy.deepcopy(self._book_data[ASK])

    def create_level(self, indicator, price):
        """
        Create a new empty price level queue.

        Parameters
        ----------
        indicator : str
            Indicate whether to create a new buy ('B') or sell ('S') price
            level.
        price : float
            Price associated with new level.

        Returns
        -------
        od : odict
            New price level queue.
        
        """

        od = odict.odict()
        self._book_data[indicator][price] = od
        self.logger.info('created new price level: %s, %f' % (indicator, price))
        return od
    
    def delete_level(self, indicator, price):
        """
        Delete an existing price level.

        Parameters
        ----------
        indicator : str
            Indicate whether to delete a buy ('B') or sell ('S') price level.
        price : float
            Price associated with level.

        """
        
        self._book_data[indicator].pop(price)
        self.logger.info('deleted price level: %s, %f' % (indicator, price))

    def delete_order(self, indicator, price, order_number):
        """
        Delete an order from a price level queue.

        Parameters
        ----------
        indicator : str
            Indicate whether to create a new buy ('B') or sell ('S') price
            level.
        price : float
            Price associated with level.
        order_number : str
            Number of order to delete.

        Notes
        -----
        If the price level queue containing the specified order is empty after
        the order is deleted, it is removed from the limit order book.
        
        """

        book = self._book_data[indicator]
        od = book[price]
        od.pop(order_number)
        self.logger.info('deleted order: %s, %s, %s' % \
                         (order_number, indicator, price))
        if not od:
            self.delete_level(indicator, price)
            
    def best_bid_price(self):
        """
        Return the best bid price defined in the book.

        Returns
        -------
        order : dict
            Limit order with best (highest) bid price.

        Notes
        -----
        Assumes that there are no empty price levels in the book.
        
        """

        prices = self._book_data[BID].keys()
        if prices == []:
            return None
        else:
            best_price = max(prices)
            if not self._book_data[BID][best_price]:
                raise RuntimeError('empty price level detected')
            return best_price

    def best_bid_quantity(self):
        """
        Return the total original and disclosed bid quantity.

        Returns
        -------
        volume_original : int
            Original volume.
        volume_disclosed : int
            Disclosed volume.
       
        """
        
        best_bid_price = self.best_bid_price()
        if best_bid_price is not None:
            od = self.price_level(BID, best_bid_price)
            volume_original = \
              sum([order['volume_original'] for order in od.itervalues()])
            volume_disclosed = \
              sum([order['volume_disclosed'] for order in od.itervalues()])
        else:
            volume_original = volume_disclosed = 0.0
        return volume_original, volume_disclosed
    
    def best_ask_price(self):
        """
        Return the best ask price defined in the book.

        Returns
        -------
        order : dict
            Limit order with best (lowest) ask price.

        Notes
        -----
        Assumes that there are no empty price levels in the book.
        
        """

        prices = self._book_data[ASK].keys()
        if prices == []:
            return None
        else:
            best_price = min(prices)
            if not self._book_data[ASK][best_price]:
                raise RuntimeError('empty price level detected')
            return best_price

    def best_ask_quantity(self):
        """
        Return the total original and disclosed ask quantity.

        Returns
        -------
        volume_original : int
            Original volume.
        volume_disclosed : int
            Disclosed volume.
       
        """
        
        best_ask_price = self.best_ask_price()
        if best_ask_price is not None:
            od = self.price_level(ASK, best_ask_price)
            volume_original = \
              sum([order['volume_original'] for order in od.itervalues()])
            volume_disclosed = \
              sum([order['volume_disclosed'] for order in od.itervalues()])
        else:
            volume_original = volume_disclosed = 0.0
        return volume_original, volume_disclosed
        
    def price_level(self, indicator, price):
        """
        Find a specified price level in the limit order book.
        
        Parameters
        ----------
        indicator : str
            Indicate whether to find a buy ('B') or sell ('S') price level.
        price : float
            Price associated with level.
        
        Returns
        -------
        od : odict.odict
            Ordered dict with matching price level.

        """

        # Validate buy/sell indicator:
        try:
            book = self._book_data[indicator]
        except KeyError:
            raise ValueError('invalid buy/sell indicator')

        # Look for price level queue:
        try:
            od = book[price]
        except KeyError:
            self.logger.info('price level not found: %s, %f' % (indicator, price))
            return None
        else:
            self.logger.info('price level found: %s, %f' % (indicator, price))
            return od

    def record_event(self, **kwargs):
        """
        This routine saves the specified event information.
        """

        # Events to record: cancel bid, cancel ask, add bid, add ask,
        # Each entry contains:
        # time, date, price, order number,
        # action (add, modify, cancel), indicator (B or S),
        # market order status (Y or N),
        # original order quantity, disclosed
        # quantity, best bid, best bid original volume, best bid disclosed volume,
        # best ask, best ask original volume, best ask disclosed volume,
        # trade data (dict)
        # If a trade has not occurred, the trade data dict is empty;
        # if a trade has occurred, the trade data dict contains:
        # trade price, trade quantity, buy order number, sell order number

        self._events[self._event_counter] = kwargs
        self._event_counter += 1
        
    def add(self, new_order):
        """
        Add the specified order to the LOB.
        
        Parameters
        ----------
        new_order : dict
            Order to add.

        Notes
        -----        
        New orders are implicitly appended onto the end of each ordered dict.
        One can obtain the oldest order by popping the first entry in the dict.
        
        """

        best_bid_volume_original, best_bid_volume_disclosed = \
          self.best_bid_quantity()
        best_ask_volume_original, best_ask_volume_disclosed = \
          self.best_ask_quantity()
        event = \
          dict(time=new_order['trans_time'],
               date=new_order['trans_date'],
               price=new_order['limit_price'],
               order_number=new_order['order_number'],
               action='add',
               indicator=new_order['buy_sell_indicator'],
               mkt_flag=new_order['mkt_flag'],
               volume_original=new_order['volume_original'],
               volume_disclosed=new_order['volume_disclosed'],
               best_bid=self.best_bid_price(),
               best_bid_volume_original=best_bid_volume_original,
               best_ask=self.best_ask_price(),
               best_ask_volume_original=best_ask_volume_original,    
               trade={})
        
        indicator = new_order['buy_sell_indicator']
        volume_original = new_order['volume_original']
        volume_disclosed = new_order['volume_disclosed']

        self.logger.info('attempting add of order: %s, %s, %f, %d, %d' % \
                         (new_order['order_number'], indicator,
                         new_order['limit_price'], volume_original,
                         volume_disclosed))
        
        # If the buy/sell order is a market order, check whether there is a
        # corresponding limit order in the book at the best ask/bid price:
        if new_order['mkt_flag'] == 'Y':
            while volume_original > 0:
                if indicator == BUY:
                    buy_order = new_order
                    best_price = self.best_ask_price()

                    # Sell/buy market orders cannot be processed until there is
                    # at least one bid/ask limit order in the book:
                    if best_price is None:
                        self.logger.info('no sell limit orders in book yet')
                    od = self.price_level(ASK, best_price) 
                elif indicator == SELL:
                    sell_order = new_order
                    best_price = self.best_bid_price()

                    # Sell/buy market orders cannot be processed until there is
                    # at least one bid/ask limit order in the book:
                    if best_price is None:
                        self.logger.info('no buy limit orders in book yet') 
                    od = self.price_level(BID, best_price)
                else:
                    RuntimeError('invalid buy/sell indicator')

                # Move through the limit orders in the price level queue from oldest
                # to newest:
                for order_number in od.keys():
                    curr_order = od[order_number]
                    if curr_order['buy_sell_indicator'] == BUY:
                        buy_order = curr_order
                    elif curr_order['buy_sell_indicator'] == SELL:
                        sell_order = curr_order
                    else:
                        RuntimeError('invalid buy/sell indicator')

                    # If a bid/ask limit order in the book has the same volume as
                    # that requested in the sell/buy market order, record a
                    # transaction and remove the limit order from the queue:
                    if curr_order['volume_original'] == volume_original:
                        self.logger.info('current limit order original volume '
                                         'vs. arriving market order original volume: '
                                         '%s = %s' % \
                                         (curr_order['volume_original'],
                                          volume_original))
                        trade = dict(trade_price=best_price,
                                     trade_quantity=volume_original,
                                     buy_order_number=buy_order['order_number'],
                                     sell_order_number=sell_order['order_number'])
                        event['trade'] = trade
                        self.record_event(**event)
                        self.delete_order(curr_order['buy_sell_indicator'],
                                          best_price, order_number)
                        volume_original = 0.0                 
                        break

                    # If a bid/ask limit order in the book has a greater volume than that
                    # requested in the sell/buy market order, record a transaction
                    # and decrement its volume accordingly:
                    elif curr_order['volume_original'] > volume_original:
                        self.logger.info('current limit order original volume '
                                         'vs. arriving market order original volume: '
                                         '%s > %s' % \
                                         (curr_order['volume_original'],
                                          volume_original))   
                        trade = dict(trade_price=best_price,
                                     trade_quantity=curr_order['volume_original']-volume_original,
                                     buy_order_number=buy_order['order_number'],
                                     sell_order_number=sell_order['order_number'])
                        event['trade'] = trade
                        self.record_event(**event)
                        curr_order['volume_original'] -= volume_original
                        volume_original = 0.0
                        break

                    # If the bid/ask limit order in the book has a volume that is
                    # below the requested sell/buy market order volume, continue
                    # removing orders from the queue until the entire requested
                    # volume has been satisfied:
                    elif curr_order['volume_original'] < volume_original:
                        self.logger.info('current limit order original volume '
                                         'vs. arriving market order original volume: '
                                         '%s < %s' % \
                                         (curr_order['volume_original'],
                                          volume_original))                  
                        trade = dict(trade_price=best_price,
                                     trade_quantity=curr_order['volume_original'],
                                     buy_order_number=buy_order['order_number'],
                                     sell_order_number=sell_order['order_number'])
                        event['trade'] = trade
                        self.record_event(**event)
                        volume_original -= curr_order['volume_original']
                        self.delete_order(curr_order['buy_sell_indicator'],
                                          best_price, order_number)
                    else:

                        # This should never be reached:
                        pass

        elif new_order['mkt_flag'] == 'N':

            # Check whether the limit order is marketable:
            price = new_order['limit_price']
            marketable = True
            if indicator == BUY and self.best_ask_price() is not None and price >= self.best_ask_price():
                self.logger.info('buy order is marketable')
                best_price = self.best_ask_price();
            elif indicator == SELL and self.best_bid_price() is not None and price <= self.best_bid_price():
                self.logger.info('sell order is marketable')
                best_price = self.best_bid_price();
            else:
                marketable = False

            # If the limit order is not marketable, add it to the appropriate
            # price level queue in the limit order book:
            if not marketable:
                self.logger.info('order is not marketable')
                od = self.price_level(indicator, price)

                # Create a new price level queue if none exists for the order's
                # limit price:
                if od is None:
                    self.logger.info('no matching price level found')
                    od = self.create_level(indicator, price)

                self.logger.info('added order: %s, %s, %s' % \
                                 (new_order['order_number'],
                                  new_order['buy_sell_indicator'],
                                  new_order['limit_price']))
                od[new_order['order_number']] = new_order
                self.record_event(**event)
                
            # Try to match marketable orders with orders that are already in the
            # book:
            else:

                # If the requested volume in the order isn't completely
                # satisfied at the best price, recompute the best price and
                # try to satisfy the remainder:
                while volume_original > 0.0:
                    if indicator == BUY:
                        buy_order = new_order                    
                        best_price = self.best_ask_price()
                        od = self.price_level(ASK, best_price) 
                    elif indicator == SELL:
                        sell_order = new_order
                        best_price = self.best_bid_price()                
                        od = self.price_level(BID, best_price)
                    else:
                        RuntimeError('invalid buy/sell indicator')

                    # Move through the limit orders in the price level queue from
                    # oldest to newest:
                    for order_number in od.keys():                    
                        curr_order = od[order_number]
                        if curr_order['buy_sell_indicator'] == BUY:
                            buy_order = curr_order
                        elif curr_order['buy_sell_indicator'] == SELL:
                            sell_order = curr_order
                        else:
                            RuntimeError('invalid buy/sell indicator')

                        # If a bid/ask limit order in the book has the same volume
                        # as that requested in the sell/buy limit order, record a
                        # transaction and remove the limit order from the queue:
                        if curr_order['volume_original'] == volume_original:
                            self.logger.info('current limit order original volume '
                                             'vs. arriving limit order original volume: '
                                             '%s = %s' % \
                                             (curr_order['volume_original'],
                                              volume_original))       
                            trade = dict(trade_price=best_price,
                                         trade_quantity=volume_original,
                                         buy_order_number=buy_order['order_number'],
                                         sell_order_number=sell_order['order_number'])
                            event['trade'] = trade
                            self.record_event(**event)
                            self.delete_order(curr_order['buy_sell_indicator'],
                                              best_price, order_number)
                            volume_original = 0.0
                            break
                        
                        # If a bid/ask limit order in the book has a greater volume
                        # than that requested in the sell/buy limit order, record a
                        # transaction and decrement its volume accordingly:
                        elif curr_order['volume_original'] > volume_original:
                            self.logger.info('current limit order original volume '
                                             'vs. arriving limit order original volume: '
                                             '%s > %s' % \
                                             (curr_order['volume_original'],
                                              volume_original))    
                            trade = dict(trade_price=best_price,
                                         trade_quantity=curr_order['volume_original']-volume_original,
                                         buy_order_number=buy_order['order_number'],
                                         sell_order_number=sell_order['order_number'])
                            event['trade'] = trade
                            self.record_event(**event)
                            curr_order['volume_original'] -= volume_original
                            volume_original = 0.0
                            break

                        # If the bid/ask limit order in the book has a volume that is
                        # below the requested sell/buy market order volume, continue
                        # removing orders from the queue until the entire requested
                        # volume has been satisfied:
                        elif curr_order['volume_original'] < volume_original:
                            self.logger.info('current limit order original volume '
                                             'vs. arriving limit order original volume: '
                                             '%s < %s' % \
                                             (curr_order['volume_original'],
                                              volume_original))     
                            trade = dict(trade_price=best_price,
                                         trade_quantity=volume_original,
                                         buy_order_number=buy_order['order_number'],
                                         sell_order_number=sell_order['order_number'])
                            event['trade'] = trade
                            self.record_event(**event)                            
                            volume_original -= curr_order['volume_original']
                            self.delete_order(curr_order['buy_sell_indicator'],
                                              best_price, order_number)
                        else:

                            # This should never be reached:
                            pass                                            
        else:
            raise RuntimeError('invalid market order flag')
        
    def modify(self, new_order):
        """
        Modify the order with matching order number in the LOB.
        """

        best_bid_volume_original, best_bid_volume_disclosed = \
          self.best_bid_quantity()
        best_ask_volume_original, best_ask_volume_disclosed = \
          self.best_ask_quantity()         
        event = \
          dict(time=new_order['trans_time'],
               date=new_order['trans_date'],
               price=new_order['limit_price'],
               order_number=new_order['order_number'],
               action='modify',
               indicator=new_order['buy_sell_indicator'],
               mkt_flag=new_order['mkt_flag'],
               volume_original=new_order['volume_original'],
               volume_disclosed=new_order['volume_disclosed'],
               best_bid=self.best_bid_price(),
               best_bid_volume_original=best_bid_volume_original,
               best_ask=self.best_ask_price(),
               best_ask_volume_original=best_ask_volume_original,    
               trade={})

        self.logger.info('attempting modify of order: %s, %s' % \
                         (new_order['order_number'],
                         new_order['buy_sell_indicator']))
        
        # This exception should never be thrown:
        if new_order['mkt_flag'] == 'Y':
            raise ValueError('cannot modify market order')
        
        od = self.price_level(new_order['buy_sell_indicator'],
                              new_order['limit_price'])
        order_number = new_order['order_number']
        if od is not None:
            self.logger.info('matching price level found: %s' % \
                             new_order['limit_price'])

            # Find the old order to modify:
            try:
                old_order = od[order_number]
            except:
                self.logger.info('order number %i not found' % order_number)
            else:

                # If the modify changes the price of an order, remove it and
                # then add the modified order to the appropriate price level queue:
                if new_order['limit_price'] != old_order['limit_price']:
                    self.logger.info('modified order %i price from %f to %f: ' % \
                                     (order_number,
                                      old_order['limit_price'],
                                      new_order['limit_price']))
                    self.delete_order(old_order['buy_sell_indicator'],
                                      old_order['limit_price'],
                                      order_number)
                    self.add(new_order)
                    
                # If the modify reduces the original or disclosed volume of an
                # order, update it without altering where it is in the queue:
                elif new_order['volume_original'] < old_order['volume_original']:
                    self.logger.info('modified order %i original volume from %f to %f: ' % \
                                     (order_number,
                                      old_order['volume_original'],
                                      new_order['volume_original']))
                    od[order_number] = new_order
                elif new_order['volume_disclosed'] < old_order['volume_disclosed']:
                    self.logger.info('modified order %i disclosed volume from %f to %f: ' % \
                                     (order_number,
                                      old_order['volume_disclosed'],
                                      new_order['volume_disclosed']))
                    od[order_number] = new_order
                    
                # If the modify increases the original or disclosed volume of an
                # order, add a order containing the difference in volume between
                # the original and new orders:
                elif new_order['volume_original'] > old_order['volume_original']:
                    self.logger.info('modified order %i original volume from %f to %f: ' % \
                                     (order_number,
                                      old_order['volume_original'],
                                      new_order['volume_original']))
                    new_order_modified = new_order.copy()
                    new_order_modified['volume_original'] -= old_order['volume_original']
                    self.add(new_order_modified)
                elif new_order['volume_disclosed'] > old_order['volume_disclosed']:
                    self.logger.info('modified order %i disclosed volume from %f to %f: ' % \
                                     (order_number,
                                      old_order['volume_disclosed'],
                                      new_order['volume_disclosed']))
                    new_order_modified = new_order.copy()
                    new_order_modified['volume_disclosed'] -= old_order['volume_disclosed']
                    self.add(new_order_modified)
                else:
                    self.logger.info('undefined modify scenario')
        else:
            self.logger.info('no matching price level found')

        self.record_event(**event)
        
    def cancel(self, order):
        """
        Remove the order with matching order number from the LOB.

        Parameters
        ----------
        order : dict
            Order to cancel.

        """
                
        best_bid_volume_original, best_bid_volume_disclosed = \
          self.best_bid_quantity()
        best_ask_volume_original, best_ask_volume_disclosed = \
          self.best_ask_quantity()         
        event = \
          dict(time=order['trans_time'],
               date=order['trans_date'],
               price=order['limit_price'],
               order_number=order['order_number'],
               action='cancel',
               indicator=order['buy_sell_indicator'],
               mkt_flag=order['mkt_flag'],
               volume_original=order['volume_original'],
               volume_disclosed=order['volume_disclosed'],
               best_bid=self.best_bid_price(),
               best_bid_volume_original=best_bid_volume_original,
               best_ask=self.best_ask_price(),
               best_ask_volume_original=best_ask_volume_original,    
               trade={})

        self.logger.info('attempting cancel of order %s' % order['order_number']) 
        
        # This exception should never be thrown:
        if order['mkt_flag'] == 'Y':
            raise ValueError('cannot cancel market order')

        indicator = order['buy_sell_indicator']
        price = order['limit_price']
        order_number = order['order_number']
        od = self.price_level(indicator, price)
        if od is not None:
            self.logger.info('matching price level found: %s, %f' % \
                             (indicator, price))
            try:
                old_order = od[order_number]            
            except:
                self.logger.info('order number %i not found' % order_number)
            else:
                self.delete_order(indicator, price, order_number)          
                self.logger.info('canceled order: %s, %s, %s' % \
                                 (order_number, indicator, price))
        else:
            self.logger.info('no matching price level found')

        best_bid_volume_original, best_bid_volume_disclosed = \
            self.best_bid_quantity()
        best_ask_volume_original, best_ask_volume_disclosed = \
            self.best_ask_quantity()            
        self.record_event(**event)
                                                    
    def print_trades(self, file_name=None):
        """
        Print trades in CSV format.

        Parameters
        ----------
        file_name : str
            Output file name. If no file is specified, the output is written to
            stdout.
        
        """

        if file_name is None:
            w = csv.writer(sys.stdout)
        else:
            f = open(file_name, 'wb')
            w = csv.writer(f)
        for entry in self._trades.iteritems():
            trade_number, trade = entry
            w.writerow([trade_number, trade['trade_date'], trade['trade_time'], \
              '%.2f' % trade['trade_price'], trade['trade_quantity'], \
              trade['buy_order_number'], trade['sell_order_number']])
        if file_name is not None:
            f.close()

    # def print_best_prices(self, indicator, file_name=None):
    #     """
    #     Print best bids or asks and their associated volumes.

    #     Parameters
    #     ----------
    #     indicator : str
    #         B for bid, S for ask.
    #     file_name : str
    #         Output file name. If no file is specified, the output is written to
    #         stdout.
        
    #     """

    #     if file_name is None:
    #         w = csv.writer(sys.stdout)
    #     else:
    #         f = open(file_name, 'wb')
    #         w = csv.writer(f)
    #     for entry in self._best_prices[indicator].iteritems():
    #         date_time = entry[0]
    #         price, volume_original, volume_disclosed = entry[1]
    #         w.writerow([date_time, price, volume_original, volume_disclosed])
    #     if file_name is not None:
    #         f.close()

    # def print_book(self, book):
    #     """
    #     Print parts of the specified book dictionary in a neat manner.
    #     """

    #     for price in map(float, sorted(book.keys(), reverse=True)):
    #         print '%06.2f: ' % price,
    #         for order_number in book[price]:
    #             order = book[price][order_number]
    #             print '(%s,%s)' % (order['volume_original'], order['volume_disclosed']),
    #         print ''

    def print_events(self, file_name=None):
        if file_name is None:
            w = csv.writer(sys.stdout)
        else:
            f = open(file_name, 'wb')
            w = csv.writer(f)
        for entry in self._events.iteritems():
            n = entry[0]
            event = entry[1]
            row = [event['time'],
                   event['date'],
                   event['price'],
                   event['order_number'],
                   event['action'],
                   event['indicator'],
                   event['mkt_flag'],
                   event['volume_original'],
                   event['volume_disclosed'],
                   event['best_bid'],
                   event['best_bid_volume_original'],
                   event['best_ask'],
                   event['best_ask_volume_original']]
            trade = event['trade']
            if trade:
                row += [trade['trade_price'],
                        trade['trade_quantity'],
                        trade['buy_order_number'],
                        trade['sell_order_number']]
            w.writerow(row)
        if file_name is not None:
            f.close()
            
if __name__ == '__main__':
    format = '%(asctime)s %(name)s %(levelname)s [%(funcName)s] %(message)s'
    logging.basicConfig(level=logging.DEBUG, format=format)
    file_name = 'AXISBANK-orders.csv'

    df = pandas.read_csv(file_name,
                         names=col_names,
                         nrows=10000)

    lob = LimitOrderBook()
    fh = logging.FileHandler('lob.log', 'w')
    fh.setFormatter(logging.Formatter(format))
    lob.logger.addHandler(fh)
    lob.process(df[0:1000])
